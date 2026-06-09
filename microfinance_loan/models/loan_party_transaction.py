from odoo import models, fields, _


class MicrofinanceLoanPartyTransaction(models.Model):
 

    _name = 'microfinance.loan.party.transaction'
    _description = 'Loan Party Transaction History'
    _order = 'date desc, id desc'

    loan_party_id = fields.Many2one(
        'microfinance.loan.party',
        string='Loan Party',
        required=True,
        ondelete='cascade',
        index=True,
    )
    date = fields.Date(string='Date', readonly=True)
    description = fields.Char(string='Description', readonly=True)
    principal = fields.Monetary(
        string='Principal', currency_field='currency_id', readonly=True)
    interest = fields.Monetary(
        string='Interest', currency_field='currency_id', readonly=True)
    due_amount = fields.Monetary(
        string='Due Amount', currency_field='currency_id', readonly=True)
    balance = fields.Monetary(
        string='Balance', currency_field='currency_id', readonly=True)
    paid_amount = fields.Monetary(
        string='Paid Amount', currency_field='currency_id', readonly=True)
    paid_date = fields.Date(string='Paid Date', readonly=True)
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    voucher_id = fields.Many2one(
        'microfinance.loan.voucher',
        string='Source Voucher',
        readonly=True,
        ondelete='cascade',
    )
