from odoo import models, fields, api, _
from odoo.exceptions import UserError


class MicrofinanceLoanVoucher(models.Model):

    _name = 'microfinance.loan.voucher'
    _description = 'Microfinance Loan Voucher'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(
        string='Voucher Reference',
        default=lambda self: _('New'),
        copy=False, readonly=True,
    )

    voucher_type = fields.Selection(
        selection=[
            ('loan_payment',     'Loan Payment (Disbursement)'),
            ('loan_receive',     'Loan Receive (Repayment)'),
            ('interest_receive', 'Interest Receive (Collection)'),
        ],
        string='Voucher Type',
        required=True,
        tracking=True,
    )

    state = fields.Selection(
        selection=[
            ('draft',     'Draft'),
            ('posted',    'Posted'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        default='draft',
        tracking=True,
        copy=False,
    )

    partner_id = fields.Many2one(
        'res.partner', string='Party (Customer)',
        required=True, tracking=True,
    )

    loan_party_id = fields.Many2one(
        'microfinance.loan.party',
        string='Loan Party Record',
        compute='_compute_loan_party', store=True,
    )

    loan_account_id = fields.Many2one(
        'account.account',
        related='loan_party_id.loan_account_id',
        string='Loan Account', store=True, readonly=True,
    )

    date = fields.Date(
        string='Transaction Date',
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )

    amount = fields.Monetary(
        string='Transaction Amount',
        required=True,
        currency_field='currency_id',
        tracking=True,
    )

    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )

    journal_id = fields.Many2one(
        'account.journal', string='Accounting Journal',
        required=True,
        domain="[('type', 'in', ['bank', 'cash', 'general'])]",
    )

    contra_account_id = fields.Many2one(
        'account.account', string='Contra Account',
        required=True,
    )

    note = fields.Text(string='Remarks')

    move_id = fields.Many2one(
        'account.move', string='Linked Journal Entry',
        readonly=True, copy=False,
    )

    installment_line_id = fields.Many2one(
        'microfinance.loan.installment.line',
        string='Linked Installment',
        readonly=True,
    )

    #  Interest metadata (set by accrual wizard) 
    interest_from_date = fields.Date(string='Interest From', readonly=True)
    interest_to_date   = fields.Date(string='Interest To',   readonly=True)
    opening_balance    = fields.Monetary(
        string='Opening Balance', currency_field='currency_id', readonly=True)
    closing_balance    = fields.Monetary(
        string='Closing Balance', currency_field='currency_id', readonly=True)
    days               = fields.Integer(string='Days', readonly=True)
    interest_rate      = fields.Float(string='Interest Rate (%)', readonly=True)
    is_compound        = fields.Boolean(string='Compound', readonly=True)

    #  Loan Disbursement fields 
    # Interest Rate & Compound: editable on disbursement, saved to Loan Party on post
    disburse_interest_rate = fields.Float(
        string='Interest Rate (%)',
        help='Set interest rate for this loan. Saved to Loan Party on posting.',
    )
    disburse_is_compound = fields.Boolean(
        string='Compound Interest',
        help='Whether interest compounds. Saved to Loan Party on posting.',
    )
    disburse_per_lac = fields.Float(
        string='Per Lac (30-day)',
        help='Interest per 100,000 for 30 days. Editable — if set manually, '
             'the Interest Rate is back-calculated from this value.',
    )
    # Loan duration: from date + to date
    loan_from_date = fields.Date(string='From Date')
    loan_to_date   = fields.Date(string='To Date')
    # Interest frequency
    interest_frequency = fields.Selection(
        selection=[
            ('daily',   'Daily'),
            ('weekly',  'Weekly'),
            ('monthly', 'Monthly'),
            ('yearly',  'Yearly'),
        ],
        string='Interest Frequency',
    )
    # Duration (read-only, computed from dates + frequency)
    loan_duration = fields.Float(
        string='Duration',
        compute='_compute_loan_duration',
        store=False,
        readonly=True,
        help='Number of periods (days/weeks/months/years) based on Interest Frequency.',
    )

    #  Computes 

    @api.depends('loan_from_date', 'loan_to_date', 'interest_frequency')
    def _compute_loan_duration(self):
        for rec in self:
            if not rec.loan_from_date or not rec.loan_to_date:
                rec.loan_duration = 0.0
                continue
            if rec.loan_to_date < rec.loan_from_date:
                rec.loan_duration = 0.0
                continue
            total_days = (rec.loan_to_date - rec.loan_from_date).days + 1
            freq = rec.interest_frequency
            if freq == 'daily':
                rec.loan_duration = float(total_days)
            elif freq == 'weekly':
                rec.loan_duration = round(total_days / 7.0, 2)
            elif freq == 'monthly':
                rec.loan_duration = round(total_days / 30.0, 2)
            elif freq == 'yearly':
                rec.loan_duration = round(total_days / 365.0, 2)
            else:
                rec.loan_duration = float(total_days)

    @api.depends('partner_id')
    def _compute_loan_party(self):
        for rec in self:
            rec.loan_party_id = (
                self.env['microfinance.loan.party'].search(
                    [('partner_id', '=', rec.partner_id.id)], limit=1
                ) if rec.partner_id else False
            )

    #  Onchange helpers 

    @api.onchange('disburse_interest_rate')
    def _onchange_disburse_interest_rate(self):
        """Auto-compute Per Lac from Interest Rate."""
        for rec in self:
            rate = rec.disburse_interest_rate
            rec.disburse_per_lac = round(
                100000.0 * (rate / 100.0) * (30.0 / 365.0), 2
            ) if rate else 0.0

    @api.onchange('disburse_per_lac')
    def _onchange_disburse_per_lac(self):
        """Back-compute Interest Rate from Per Lac when user edits Per Lac manually.
        Formula: per_lac = 100000 * (rate/100) * (30/365)
                 → rate = per_lac * 365 / 30000
        """
        for rec in self:
            if rec.disburse_per_lac:
                rec.disburse_interest_rate = round(
                    rec.disburse_per_lac * 365.0 / 30000.0, 4
                )
            else:
                rec.disburse_interest_rate = 0.0

    @api.onchange('partner_id', 'voucher_type')
    def _onchange_partner_auto_amount(self):
        """Auto-fill Transaction Amount with outstanding/uncollected balance when party is selected."""
        for rec in self:
            if not rec.partner_id or rec.voucher_type not in ('loan_receive', 'interest_receive'):
                continue
            loan_party = self.env['microfinance.loan.party'].search(
                [('partner_id', '=', rec.partner_id.id)], limit=1
            )
            if not loan_party:
                continue
            if rec.voucher_type == 'loan_receive':
                outstanding = loan_party.outstanding_loan_balance
                if outstanding > 0:
                    rec.amount = outstanding
            elif rec.voucher_type == 'interest_receive':
                uncollected = loan_party.uncollected_interest
                if uncollected > 0:
                    rec.amount = uncollected

    #  CRUD 

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code(
                        'microfinance.loan.voucher') or _('New')
                )
        records = super().create(vals_list)
        for rec in records:
            rec._check_duplicate_interest()
        return records

    #  Validation 

    def _validate_before_post(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_('Only draft vouchers can be posted.'))
        if self.amount <= 0:
            raise UserError(_('Amount must be greater than zero.'))
        if not self.loan_party_id:
            raise UserError(
                _('No Loan Party found for "%s". Create a Loan Party first.')
                % self.partner_id.name)
        if not self.loan_account_id:
            raise UserError(
                _('Loan Party "%s" has no sub-ledger account.')
                % self.partner_id.name)

        # Disbursement: interest rate is required
        if self.voucher_type == 'loan_payment' and not self.interest_from_date:
            if not self.disburse_interest_rate:
                raise UserError(_('Please set the Interest Rate before posting a disbursement.'))

        # Block Repayment if Outstanding Balance = 0
        if self.voucher_type == 'loan_receive':
            outstanding = self.loan_party_id.outstanding_loan_balance
            if outstanding <= 0:
                raise UserError(
                    _('Cannot post Loan Repayment.\n\n'
                      'Outstanding Loan Balance for "%s" is 0.00.\n'
                      'There is no outstanding loan to repay.')
                    % self.partner_id.name
                )
            if self.amount > outstanding:
                raise UserError(
                    _('Repayment amount (%.2f) exceeds the Outstanding Loan Balance (%.2f) for "%s".\n'
                      'You cannot repay more than what is currently owed.')
                    % (self.amount, outstanding, self.partner_id.name)
                )

        # Block Interest Collection if Uncollected Interest = 0
        if self.voucher_type == 'interest_receive':
            uncollected = self.loan_party_id.uncollected_interest
            if uncollected <= 0:
                raise UserError(
                    _('Cannot post Interest Collection.\n\n'
                      'Uncollected Interest for "%s" is 0.00.\n'
                      'Please calculate interest first before collecting.')
                    % self.partner_id.name
                )
            total_accrued = self.loan_party_id.total_interest_accrued
            if self.amount > total_accrued:
                raise UserError(
                    _('Interest collection amount (%.2f) exceeds the Total Interest Accrued (%.2f) for "%s".\n'
                      'You cannot collect more interest than what has been accrued.')
                    % (self.amount, total_accrued, self.partner_id.name)
                )

        # Duplicate interest check
        self._check_duplicate_interest()

    def _check_duplicate_interest(self):
        """
        Block duplicate interest for same party + overlapping date range.
        Covers both accrual entries and interest_receive vouchers.
        """
        for rec in self:
            is_accrual = (
                rec.voucher_type == 'loan_payment'
                and rec.interest_from_date and rec.interest_to_date
            )
            is_collection = (
                rec.voucher_type == 'interest_receive'
                and rec.interest_from_date and rec.interest_to_date
            )
            if not (is_accrual or is_collection):
                continue
            if not rec.loan_party_id:
                continue

            domain = [
                ('id',                 '!=',  rec.id),
                ('voucher_type',       '=',   rec.voucher_type),
                ('loan_party_id',      '=',   rec.loan_party_id.id),
                ('state',              'in',  ['draft', 'posted']),
                ('interest_from_date', '<=',  rec.interest_to_date),
                ('interest_to_date',   '>=',  rec.interest_from_date),
            ]
            if is_accrual:
                domain.append(('interest_from_date', '!=', False))

            dup = self.env['microfinance.loan.voucher'].search(domain, limit=1)
            if dup:
                label = _('Interest Accrual') if is_accrual else _('Interest Collection')
                raise UserError(
                    _('%s for "%s" already exists for this or an overlapping date range.\n\n'
                      'Conflicting voucher: %s (%s → %s)\n\n'
                      'Please change the date range.')
                    % (label,
                       rec.loan_party_id.partner_id.name,
                       dup.name,
                       dup.interest_from_date,
                       dup.interest_to_date)
                )

    #  Posting 

    def _prepare_move_vals(self):
        self.ensure_one()
        if self.voucher_type == 'loan_payment':
            dr_acct = self.loan_account_id
            cr_acct = self.contra_account_id
        else:
            dr_acct = self.contra_account_id
            cr_acct = self.loan_account_id

        label = self.note or self.name
        return {
            'ref':        self.name,
            'date':       self.date,
            'journal_id': self.journal_id.id,
            'narration':  self.note,
            'line_ids': [
                (0, 0, {
                    'account_id': dr_acct.id,
                    'partner_id': self.partner_id.id,
                    'name':       label,
                    'debit':      self.amount,
                    'credit':     0.0,
                }),
                (0, 0, {
                    'account_id': cr_acct.id,
                    'partner_id': self.partner_id.id,
                    'name':       label,
                    'debit':      0.0,
                    'credit':     self.amount,
                }),
            ],
        }

    def action_post(self):
        for rec in self:
            rec._validate_before_post()
            move = self.env['account.move'].create(rec._prepare_move_vals())
            move.action_post()
            rec.write({'move_id': move.id, 'state': 'posted'})
            if not self.env.context.get('skip_installment_link'):
                rec._link_to_installment()
            # On disbursement: save interest rate & compound to Loan Party
            if (rec.voucher_type == 'loan_payment'
                    and not rec.interest_from_date
                    and rec.loan_party_id):
                update_vals = {}
                if rec.disburse_interest_rate:
                    update_vals['interest_rate'] = rec.disburse_interest_rate
                update_vals['is_compound_interest'] = rec.disburse_is_compound
                rec.loan_party_id.write(update_vals)
            # Sync transaction history row
            rec._sync_party_transaction()

    #  Other Actions 

    def _link_to_installment(self):
        self.ensure_one()
        if self.voucher_type not in ('loan_receive', 'interest_receive'):
            return
        InstLine = self.env['microfinance.loan.installment.line']
        line = InstLine.search([
            ('loan_id.partner_id', '=', self.partner_id.id),
            ('loan_id.state', '=', 'running'),
            ('date', '<=', self.date),
            ('state', '!=', 'paid'),
        ], limit=1)
        if not line:
            return
        link_field = (
            'principal_voucher_id'
            if self.voucher_type == 'loan_receive'
            else 'interest_voucher_id'
        )
        line.write({link_field: self.id})
        self.write({'installment_line_id': line.id})
        line._check_and_mark_paid_from_vouchers()

    def action_cancel(self):
        for rec in self:
            if rec.state == 'posted' and rec.move_id:
                rec.move_id.button_cancel()
            rec.state = 'cancelled'

    def action_reset_draft(self):
        for rec in self:
            if rec.state == 'cancelled':
                rec.move_id = False
                rec.state = 'draft'

    def action_view_journal_entry(self):
        self.ensure_one()
        return {
            'type':      'ir.actions.act_window',
            'name':      _('Journal Entry'),
            'res_model': 'account.move',
            'res_id':    self.move_id.id,
            'view_mode': 'form',
        }

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        partner_id  = res.get('partner_id')
        voucher_type = res.get('voucher_type')
        if partner_id:
            loan = self.env['microfinance.loan.installment'].search([
                ('partner_id', '=', partner_id),
                ('state', '=', 'running'),
            ], limit=1)
            if loan:
                if voucher_type == 'loan_payment':
                    res.update({
                        'amount':             loan.principal_amount,
                        'journal_id':         loan.journal_id.id,
                        'contra_account_id':  loan.disbursement_account_id.id,
                    })
                else:
                    res.update({
                        'journal_id':         loan.journal_id.id,
                        'contra_account_id':  loan.disbursement_account_id.id,
                    })
        return res

    #  Transaction History Sync 

    def _sync_party_transaction(self):
        """Upsert one transaction history row per posted voucher."""
        Transaction = self.env['microfinance.loan.party.transaction']
        for rec in self:
            if not rec.loan_party_id:
                continue

            if rec.voucher_type == 'loan_payment' and not rec.interest_from_date:
                principal  = rec.amount
                interest   = 0.0
                due_amount = rec.amount
                desc       = _('Loan Disbursement')
            elif rec.voucher_type == 'loan_receive':
                principal  = rec.amount
                interest   = 0.0
                due_amount = rec.amount
                desc       = _('Principal Repayment')
            elif rec.voucher_type == 'interest_receive':
                principal  = 0.0
                interest   = rec.amount
                due_amount = rec.amount
                desc       = _('Interest Collection (%s to %s)') % (
                    rec.interest_from_date or '', rec.interest_to_date or '')
            elif rec.voucher_type == 'loan_payment' and rec.interest_from_date:
                principal  = 0.0
                interest   = rec.amount
                due_amount = rec.amount
                desc       = _('Interest Accrual (%s to %s)') % (
                    rec.interest_from_date, rec.interest_to_date)
            else:
                continue

            aml = self.env['account.move.line'].search([
                ('account_id',    '=',  rec.loan_party_id.loan_account_id.id),
                ('move_id.state', '=',  'posted'),
                ('date',          '<=', rec.date),
            ])
            balance = sum(aml.mapped('debit')) - sum(aml.mapped('credit'))

            vals = {
                'loan_party_id': rec.loan_party_id.id,
                'date':          rec.date,
                'description':   desc,
                'principal':     principal,
                'interest':      interest,
                'due_amount':    due_amount,
                'balance':       balance,
                'paid_amount':   rec.amount if rec.state == 'posted' else 0.0,
                'paid_date':     rec.date   if rec.state == 'posted' else False,
                'voucher_id':    rec.id,
            }
            existing = Transaction.search([('voucher_id', '=', rec.id)], limit=1)
            if existing:
                existing.write(vals)
            else:
                Transaction.create(vals)
