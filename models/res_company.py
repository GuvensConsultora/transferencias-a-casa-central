# -*- coding: utf-8 -*-
# Extensión mínima de compañía para parametrizar el diario central y la cuenta transitoria.
# Mantener la configuración a nivel compañía evita "magia" en diarios.
from odoo import fields, models

class ResCompany(models.Model):
    _inherit = "res.company"

    # Diario donde se registrará el asiento de la transferencia (lado "Casa Central")
    central_cash_journal_id = fields.Many2one(
        "account.journal",
        string="Diario Efectivo Central",
        domain="[('type','in',('cash','bank')), ('company_id','=', id)]",
        help="Diario de banco/caja de Casa Central donde se postean las transferencias."
    )

    # Cuenta transitoria usada como DEBE al validar (contrapartida del haber de la caja origen)
    central_transit_account_id = fields.Many2one(
        "account.account",
        string="Cuenta Transitoria Central",
        domain="[('deprecated','=',False), ('company_id','=', id)]",
        help="Cuenta de tránsito para recibir el débito del asiento de transferencia."
    )