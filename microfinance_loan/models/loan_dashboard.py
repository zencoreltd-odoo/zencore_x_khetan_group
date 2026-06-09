from odoo import models, fields, api, _


class MicrofinanceDashboard(models.Model):

    _name = 'microfinance.loan.dashboard'
    _description = 'Microfinance Loan Dashboard'
    _auto = False
    _order = 'customer_name'

    partner_id           = fields.Many2one('res.partner',          readonly=True)
    loan_party_id        = fields.Many2one('microfinance.loan.party', readonly=True,
                                           string='Loan Party')
    customer_name        = fields.Char(string='Customer Name',     readonly=True)
    loan_account_id      = fields.Many2one('account.account',      readonly=True)
    interest_rate        = fields.Float(string='Interest Rate (%)', readonly=True)
    is_compound_interest = fields.Boolean(string='Compound',       readonly=True)

    loan_amount      = fields.Float(string='Loan Amount',       readonly=True)
    receive_amount   = fields.Float(string='Receive Amount',    readonly=True)
    interest_amount  = fields.Float(string='Interest Amount',   readonly=True)
    interest_received = fields.Float(string='Interest Received', readonly=True)
    closing_balance  = fields.Float(string='Closing Balance',   readonly=True)

    @property
    def _table_query(self):
        return """
            SELECT
                lp.id                        AS id,
                lp.id                        AS loan_party_id,
                lp.partner_id                AS partner_id,
                rp.name                      AS customer_name,
                lp.loan_account_id           AS loan_account_id,
                lp.interest_rate             AS interest_rate,
                lp.is_compound_interest      AS is_compound_interest,

                COALESCE((
                    SELECT SUM(aml.debit) - SUM(aml.credit)
                    FROM   account_move_line aml
                    JOIN   account_move am ON am.id = aml.move_id
                    WHERE  aml.account_id = lp.loan_account_id
                      AND  am.state = 'posted'
                ), 0.0)                      AS loan_amount,

                COALESCE((
                    SELECT SUM(aml.debit) - SUM(aml.credit)
                    FROM   account_move_line aml
                    JOIN   account_move am ON am.id = aml.move_id
                    WHERE  aml.account_id = lp.loan_account_id
                      AND  am.state = 'posted'
                ), 0.0)                      AS closing_balance,

                COALESCE((
                    SELECT SUM(v.amount)
                    FROM   microfinance_loan_voucher v
                    WHERE  v.loan_party_id = lp.id
                      AND  v.voucher_type = 'loan_receive'
                      AND  v.state = 'posted'
                ), 0.0)                      AS receive_amount,

                COALESCE((
                    SELECT SUM(v.amount)
                    FROM   microfinance_loan_voucher v
                    WHERE  v.loan_party_id = lp.id
                      AND  v.voucher_type = 'loan_payment'
                      AND  v.interest_from_date IS NOT NULL
                      AND  v.state = 'posted'
                ), 0.0)                      AS interest_amount,

                COALESCE((
                    SELECT SUM(v.amount)
                    FROM   microfinance_loan_voucher v
                    WHERE  v.loan_party_id = lp.id
                      AND  v.voucher_type = 'interest_receive'
                      AND  v.state = 'posted'
                ), 0.0)                      AS interest_received

            FROM  microfinance_loan_party lp
            JOIN  res_partner rp ON rp.id = lp.partner_id
            WHERE lp.active = TRUE
              AND lp.loan_account_id IS NOT NULL
        """

    def open_party_transactions(self):
        
        self.ensure_one()
        party = self.env['microfinance.loan.party'].browse(self.loan_party_id.id)
        if not party.exists():
            return {}
        return {
            'type':      'ir.actions.act_window',
            'name':      party.display_name,
            'res_model': 'microfinance.loan.party',
            'res_id':    party.id,
            'view_mode': 'form',
            'views':     [(False, 'form')],
            'target':    'current',
            'context':   {'active_tab': 'transactions'},
        }
