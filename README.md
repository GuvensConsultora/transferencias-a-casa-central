# Transferencias a Casa Central (Odoo 17)

Módulo para registrar transferencias de efectivo desde diarios locales a Casa Central,
validando importe y generando asiento misceláneo.

## Parametrización
- **Compañía**: Diario Efectivo Central + Cuenta Transitoria Central.
- **Diario Origen**: tener cuenta principal (default_account_id o payment_*).

## Flujo
1. Se propone el diario origen automáticamente y se calcula el saldo (`amount_system`).
2. Ingresás `amount_input`. Si hay diferencia, `reason` obligatorio.
3. Validar genera asiento en el diario central: Debe = cuenta transitoria, Haber = cuenta principal del diario origen.

## Odoo 17
- Vistas usan **modifiers** JSON, no `attrs`/`states`.
- En journals no existen default_debit/credit; se usa `default_account_id`/`payment_*`.