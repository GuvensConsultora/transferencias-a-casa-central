# -*- coding: utf-8 -*-
# Modelo operativo para registrar transferencias desde un diario local a Casa Central.
# Odoo 17: no existen default_debit_account_id/default_credit_account_id en journal.
# Se usa una heurística de cuenta principal y una cuenta transitoria parametrizada en compañía.

from odoo import api, fields, models, _
from odoo.exceptions import UserError

class TransferCentral(models.Model):
    _name = "transfer.central"
    _description = "Transferencias a Casa Central"
    _order = "date desc, id desc"

    # -------------------
    # Campos de negocio
    # -------------------
    date = fields.Date(
        default=fields.Date.context_today,
        required=True,
        string="Fecha",
        help="Fecha contable del asiento que se generará al validar."
    )

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda s: s.env.company,
        help="Compañía responsable de la operación."
    )

    # Diario origen (sólo lectura por requerimiento). Se setea automáticamente en default_get.
    journal_from_id = fields.Many2one(
        "account.journal",
        string="Desde",
        required=True,
        readonly=True,
        help="Diario de origen (caja/banco) filtrado por compañía y (opcional) OU."
    )

    # Diario central (solo lectura), tomado de la compañía
    journal_central_id = fields.Many2one(
        "account.journal",
        string="Diario Efectivo Central",
        related="company_id.central_cash_journal_id",
        store=True,
        readonly=True,
    )

    # Importe calculado automáticamente al abrir: saldo del mayor de la cuenta principal del diario origen
    amount_system = fields.Float(string="Importe (sistema)", readonly=True)

    # Importe informado por el usuario (lo que dice que va a transferir)
    amount_input = fields.Float(string="Importe informado")

    # Diferencia on-the-fly: lo que el sistema dice que hay vs. lo que informan
    difference = fields.Float(string="Diferencia", compute="_compute_difference", store=False)

    # Motivo obligatorio cuando hay diferencia (según requerimiento)
    reason = fields.Text(string="Motivo diferencia")

    # Estados de flujo
    state = fields.Selection(
        [("draft", "Borrador"), ("validated", "Validado")],
        default="draft",
        tracking=True,
    )

    # -------------------
    # Cómputos
    # -------------------
    @api.depends("amount_system", "amount_input")
    def _compute_difference(self):
        """Calcula diferencia sin persistir, para simplificar la UI."""
        for rec in self:
            rec.difference = (rec.amount_system or 0.0) - (rec.amount_input or 0.0)

    # -------------------
    # Dominios / defaults
    # -------------------
    def _domain_journal_from(self):
        """Diarios elegibles como 'Desde': caja/banco de la compañía actual.
        Si existen OU (OCA) se filtra por OU del usuario también.
        """
        domain = [("type", "in", ("cash", "bank")), ("company_id", "=", self.env.company.id)]
        if "operating_unit_id" in self.env["account.journal"]._fields and getattr(self.env.user, "operating_unit_ids", False):
            domain += [("operating_unit_id", "in", self.env.user.operating_unit_ids.ids)]
        return domain

    @api.model
    def default_get(self, fields_list):
        """Asigna journal_from_id automáticamente y calcula amount_system de entrada.
        Este campo es readonly por consigna, así evitamos que quede vacío.
        """
        vals = super().default_get(fields_list)
        journal = self.env["account.journal"].search(self._domain_journal_from(), limit=1)
        if journal:
            vals.setdefault("journal_from_id", journal.id)
            vals["amount_system"] = self._compute_journal_balance(journal)
        else:
            # Mensaje temprano y claro si no hay diario elegible
            if "journal_from_id" in fields_list:
                raise UserError(_("No se encontró un Diario 'Desde' habilitado para este usuario/compañía."))
        return vals

    # -------------------
    # Utilidades contables
    # -------------------
    def _get_journal_main_account(self, journal):
        """Obtiene la cuenta 'principal' del diario en Odoo 17.
        Prioridad: default_account_id > payment_debit_account_id > payment_credit_account_id.
        Esto reemplaza a los viejos default_debit/credit_account_id de v16.
        """
        if not journal:
            return False
        J = self.env["account.journal"]
        if "default_account_id" in J._fields and journal.default_account_id:
            return journal.default_account_id
        if "payment_debit_account_id" in J._fields and journal.payment_debit_account_id:
            return journal.payment_debit_account_id
        if "payment_credit_account_id" in J._fields and journal.payment_credit_account_id:
            return journal.payment_credit_account_id
        return False

    def _compute_journal_balance(self, journal):
        """Calcula el saldo del mayor de la cuenta principal del diario 'journal' hasta hoy.
        Solo considera movimientos posteados para evitar ruido de borradores.
        """
        account = self._get_journal_main_account(journal)
        if not account:
            return 0.0
        aml = self.env["account.move.line"].read_group(
            domain=[
                ("account_id", "=", account.id),
                ("company_id", "=", journal.company_id.id),
                ("parent_state", "=", "posted"),
                ("date", "<=", fields.Date.context_today(self)),
            ],
            fields=["balance:sum"],
            groupby=[],
        )
        return aml[0]["balance"] if aml else 0.0

    # -------------------
    # Validación y asiento
    # -------------------
    def _check_pre_validation(self):
        """Validaciones previas a crear el asiento: parametrización y coherencia."""
        for rec in self:
            if not rec.journal_from_id:
                raise UserError(_("Debe estar definido el Diario 'Desde' (se asigna automáticamente)."))
            if not rec.journal_central_id:
                raise UserError(_("Configure el 'Diario Efectivo Central' en la compañía."))
            # Si hay diferencia, motivo obligatorio
            if round(rec.difference, 2) != 0.0 and not rec.reason:
                raise UserError(_("Existe diferencia. Debe indicar el motivo."))
            # Política clara: la cuenta transitoria es parámetro de compañía
            if not rec.company_id.central_transit_account_id:
                raise UserError(_("Configure la 'Cuenta Transitoria Central' en la compañía."))

    def action_validate(self):
        """Crea un asiento misceláneo en el diario central:
           Debe: cuenta transitoria central (compañía)
           Haber: cuenta principal del diario origen
        """
        self._check_pre_validation()
        for rec in self:
            amount = rec.amount_input or 0.0
            if amount <= 0:
                raise UserError(_("El importe informado debe ser mayor a cero."))

            # Cuenta a acreditar: principal del diario origen
            credit_account = rec._get_journal_main_account(rec.journal_from_id)
            if not credit_account:
                raise UserError(_("El diario 'Desde' no tiene cuenta principal configurada (default/payment_*)."))

            # Cuenta a debitar: transitoria de la compañía
            debit_account = rec.company_id.central_transit_account_id

            move_vals = {
                "date": rec.date,
                "journal_id": rec.journal_central_id.id,  # asiento se registra en el diario central
                "ref": _("Transferencia a Casa Central #%s") % rec.id,
                "line_ids": [
                    # Debe transitoria central
                    (0, 0, {
                        "name": _("Transferencia desde %s") % rec.journal_from_id.name,
                        "account_id": debit_account.id,
                        "debit": amount if amount > 0 else 0.0,
                        "credit": 0.0,
                        "company_id": rec.company_id.id,
                    }),
                    # Haber caja/banco origen
                    (0, 0, {
                        "name": _("Salida de %s") % rec.journal_from_id.name,
                        "account_id": credit_account.id,
                        "debit": 0.0,
                        "credit": amount if amount > 0 else 0.0,
                        "company_id": rec.company_id.id,
                    }),
                ],
                "company_id": rec.company_id.id,
            }
            move = self.env["account.move"].create(move_vals)
            move.action_post()
            rec.state = "validated"
        return True