from odoo import models, fields, api, _
from odoo.exceptions import UserError


class MicrofinanceLoanParty(models.Model):

    _name = 'microfinance.loan.party'
    _description = 'Microfinance Loan Party'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'display_name'

    # Basic Info 
    partner_id = fields.Many2one(
        'res.partner',
        string='Party Name',
        required=True,
        tracking=True,
    )

    display_name = fields.Char(
        string='Name',
        compute='_compute_display_name',
        store=True,
    )

    creation_date = fields.Date(
        string='Creation Date',
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )

    #  Currency 
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id,
        readonly=True,
    )

    #  Account Configuration 
    parent_account_id = fields.Many2one(
        'account.account',
        string='Parent Account / Ledger',
        required=True,
        tracking=True,
    )

    account_type = fields.Selection(
        selection=[
            ('asset_current',         'Current Asset'),
            ('asset_non_current',     'Non-current Asset'),
            ('asset_fixed',           'Fixed Asset'),
            ('asset_prepayments',     'Prepayments'),
            ('liability_current',     'Current Liability'),
            ('liability_non_current', 'Non-current Liability'),
            ('equity',                'Equity'),
            ('income',                'Income'),
            ('expense',               'Expense'),
            ('off_balance',           'Off Balance'),
        ],
        string='Account Type',
        required=True,
        tracking=True,
    )

    loan_account_id = fields.Many2one(
        'account.account',
        string='Loan Account (Sub-ledger)',
        tracking=True,
        readonly=True,
    )

    interest_rate = fields.Float(
        string='Interest Rate (%)',
        default=0.0,
        tracking=True,
    )

    is_compound_interest = fields.Boolean(
        string='Compound Interest',
        default=False,
        tracking=True,
    )

    active = fields.Boolean(default=True)

    #  SQL Constraints 
    _sql_constraints = [
        ('partner_unique', 'UNIQUE(partner_id)',
         'A loan party already exists for this partner!'),
    ]

    #  Computes 
    @api.depends('partner_id', 'loan_account_id')
    def _compute_display_name(self):
        for rec in self:
            name = rec.partner_id.name or 'New Party'
            if rec.loan_account_id:
                name += ' [%s]' % rec.loan_account_id.code
            rec.display_name = name

    # Loan Summary Computed Fields 

    total_loan_disbursed = fields.Monetary(
        string='Total Loan Disbursed',
        compute='_compute_loan_summary',
        currency_field='currency_id',
        store=False,
    )
    total_principal_received = fields.Monetary(
        string='Total Principal Received',
        compute='_compute_loan_summary',
        currency_field='currency_id',
        store=False,
    )
    outstanding_loan_balance = fields.Monetary(
        string='Outstanding Loan Balance',
        compute='_compute_loan_summary',
        currency_field='currency_id',
        store=False,
    )
    total_interest_accrued = fields.Monetary(
        string='Total Interest Accrued',
        compute='_compute_loan_summary',
        currency_field='currency_id',
        store=False,
    )
    total_interest_collected = fields.Monetary(
        string='Total Interest Collected',
        compute='_compute_loan_summary',
        currency_field='currency_id',
        store=False,
    )
    uncollected_interest = fields.Monetary(
        string='Uncollected Interest',
        compute='_compute_loan_summary',
        currency_field='currency_id',
        store=False,
    )
    per_lac_amount = fields.Float(
        string='Per Lac Amount',
        compute='_compute_loan_summary',
        store=False,
        help='Interest per 100,000 at current rate for 30 days (indicative).',
    )

    # Transaction history (populated by voucher._sync_party_transaction)
    loan_transaction_ids = fields.One2many(
        'microfinance.loan.party.transaction',
        'loan_party_id',
        string='Transaction History',
        readonly=True,
    )

    @api.depends('loan_account_id', 'interest_rate')
    def _compute_loan_summary(self):
        Voucher = self.env['microfinance.loan.voucher']
        for rec in self:
            if not rec.loan_account_id:
                rec.total_loan_disbursed = 0.0
                rec.total_principal_received = 0.0
                rec.outstanding_loan_balance = 0.0
                rec.total_interest_accrued = 0.0
                rec.total_interest_collected = 0.0
                rec.uncollected_interest = 0.0
                rec.per_lac_amount = 0.0
                continue

            # Disbursements (loan_payment without interest_from_date)
            disbursed = sum(Voucher.search([
                ('loan_party_id', '=', rec.id),
                ('voucher_type', '=', 'loan_payment'),
                ('interest_from_date', '=', False),
                ('state', '=', 'posted'),
            ]).mapped('amount'))

            # Principal repayments
            principal_received = sum(Voucher.search([
                ('loan_party_id', '=', rec.id),
                ('voucher_type', '=', 'loan_receive'),
                ('state', '=', 'posted'),
            ]).mapped('amount'))

            # Interest accrued (accrual entries have interest_from_date)
            interest_accrued = sum(Voucher.search([
                ('loan_party_id', '=', rec.id),
                ('voucher_type', '=', 'loan_payment'),
                ('interest_from_date', '!=', False),
                ('state', '=', 'posted'),
            ]).mapped('amount'))

            # Interest collected
            interest_collected = sum(Voucher.search([
                ('loan_party_id', '=', rec.id),
                ('voucher_type', '=', 'interest_receive'),
                ('state', '=', 'posted'),
            ]).mapped('amount'))

            # Per lac (30-day indicative)
            per_lac = round(
                100000.0 * (rec.interest_rate / 100.0) * (30.0 / 365.0), 2
            ) if rec.interest_rate else 0.0

            rec.total_loan_disbursed = disbursed
            rec.total_principal_received = principal_received
            rec.outstanding_loan_balance = disbursed - principal_received
            rec.total_interest_accrued = interest_accrued
            rec.total_interest_collected = interest_collected
            rec.uncollected_interest = max(0.0, interest_accrued - interest_collected)
            rec.per_lac_amount = per_lac

    #  Business Logic 

    def action_create_loan_account(self):
        """Auto-create sub-ledger account. Called on save AND available as a button."""
        self.ensure_one()
        if self.loan_account_id:
            raise UserError(_('A loan account already exists for this party.'))

        existing_codes = self.env['account.account'].search(
            [('code', 'like', self.parent_account_id.code + '%')]
        ).mapped('code')
        counter = 1
        while True:
            new_code = '%s%s' % (
                self.parent_account_id.code, str(counter).zfill(3)
            )
            if new_code not in existing_codes:
                break
            counter += 1

        account = self.env['account.account'].create({
            'name': 'Loan - %s' % self.partner_id.name,
            'code': new_code,
            'account_type': self.account_type,
        })
        self.loan_account_id = account.id
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Loan sub-ledger account %s created.') % account.code,
                'type': 'success',
                'sticky': False,
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-create sub-ledger on party save (MOD-01: sub-ledger still created automatically)."""
        records = super().create(vals_list)
        for rec in records:
            if rec.parent_account_id and rec.account_type and not rec.loan_account_id:
                rec.action_create_loan_account()
        return records
