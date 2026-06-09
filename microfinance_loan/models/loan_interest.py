from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import timedelta
import calendar


class MicrofinanceLoanInterest(models.TransientModel):
    
    _name = 'microfinance.loan.interest'
    _description = 'Loan Interest Calculation Wizard'

    #Parameters 
    partner_id = fields.Many2one(
        'res.partner', string='Party',
        help='Leave empty to calculate for all active parties.',
    )

    date_from = fields.Date(
        string='From Date', required=True,
        help='Start of calculation period.',
    )

    date_to = fields.Date(
        string='To Date', required=True,
        default=fields.Date.context_today,
        help='End of calculation period (inclusive).',
    )

    journal_id = fields.Many2one(
        'account.journal', string='Journal', required=True,
        domain="[('type', '=', 'general')]",
    )

    interest_income_account_id = fields.Many2one(
        'account.account',
        string='Interest Income Account',
        required=True,
        help='Cr side for interest accrual journal entry.',
    )

    # Results 
    line_ids = fields.One2many(
        'microfinance.loan.interest.line', 'wizard_id',
        string='Interest Lines', readonly=True,
    )

    computed = fields.Boolean(default=False)

    # Summary computed fields
    total_disbursed = fields.Float(
        string='Total Disbursed', compute='_compute_totals', store=False)
    total_interest = fields.Float(
        string='Total Interest', compute='_compute_totals', store=False)
    total_per_lac = fields.Float(
        string='Total Per Lac', compute='_compute_totals', store=False)

    @api.depends('line_ids', 'line_ids.interest_amount',
                 'line_ids.closing_balance', 'line_ids.per_lac_interest')
    def _compute_totals(self):
        for rec in self:
            rec.total_disbursed = sum(rec.line_ids.mapped('closing_balance'))
            rec.total_interest  = sum(rec.line_ids.mapped('interest_amount'))
            rec.total_per_lac   = sum(rec.line_ids.mapped('per_lac_interest'))

    #  Helpers 
    def _get_balance_at(self, loan_account_id, as_of_date):
        lines = self.env['account.move.line'].search([
            ('account_id',    '=', loan_account_id),
            ('move_id.state', '=', 'posted'),
            ('date',          '<=', as_of_date),
        ])
        return sum(lines.mapped('debit')) - sum(lines.mapped('credit'))

    def _get_unpaid_interest(self, party):
        accrued = self.env['microfinance.loan.voucher'].search([
            ('loan_party_id', '=', party.id),
            ('voucher_type',  '=', 'interest_receive'),
            ('state',         '=', 'posted'),
            ('interest_from_date', '!=', False),
        ])
        collected = self.env['microfinance.loan.voucher'].search([
            ('loan_party_id', '=', party.id),
            ('voucher_type',  '=', 'interest_receive'),
            ('state',         '=', 'posted'),
            ('interest_from_date', '=', False),
        ])
        return max(0.0, sum(accrued.mapped('amount')) - sum(collected.mapped('amount')))

    def _calc_days(self, date_from, date_to):
        return (date_to - date_from).days + 1

    def _calc_interest(self, balance, rate_pct, days):
        if balance <= 0 or rate_pct <= 0 or days <= 0:
            return 0.0
        return round(balance * (rate_pct / 100.0) * (days / 365.0), 2)

    def _calc_per_lac(self, rate_pct, days):
        return self._calc_interest(100000.0, rate_pct, days)

    #  Actions 
    def action_compute(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_('From Date must be before To Date.'))

        days = self._calc_days(self.date_from, self.date_to)

        domain = [('loan_account_id', '!=', False), ('active', '=', True)]
        if self.partner_id:
            domain.append(('partner_id', '=', self.partner_id.id))
        parties = self.env['microfinance.loan.party'].search(domain)

        if not parties:
            raise UserError(_('No active loan parties with loan accounts found.'))

        self.line_ids.unlink()

        lines_vals = []
        for party in parties:
            day_before      = self.date_from - timedelta(days=1)
            opening_balance = self._get_balance_at(party.loan_account_id.id, day_before)
            closing_balance = self._get_balance_at(party.loan_account_id.id, self.date_to)

            effective_balance = closing_balance
            unpaid_interest   = 0.0
            if party.is_compound_interest:
                unpaid_interest   = self._get_unpaid_interest(party)
                effective_balance = closing_balance + unpaid_interest

            interest_amount = self._calc_interest(effective_balance, party.interest_rate, days)
            per_lac         = self._calc_per_lac(party.interest_rate, days)
            cumulative      = opening_balance + interest_amount

            lines_vals.append({
                'wizard_id':           self.id,
                'loan_party_id':       party.id,
                'opening_balance':     opening_balance,
                'closing_balance':     closing_balance,
                'unpaid_interest':     unpaid_interest,
                'effective_balance':   effective_balance,
                'interest_rate':       party.interest_rate,
                'is_compound':         party.is_compound_interest,
                'days':                days,
                'interest_amount':     interest_amount,
                'per_lac_interest':    per_lac,
                'cumulative_interest': cumulative,
            })

        self.env['microfinance.loan.interest.line'].create(lines_vals)
        self.computed = True

        return {
            'type':      'ir.actions.act_window',
            'res_model': self._name,
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }

    def action_post_interest(self):
        self.ensure_one()
        if not self.computed:
            raise UserError(_('Please compute interest first.'))

        lines = self.line_ids.filtered(lambda l: l.interest_amount > 0)
        if not lines:
            raise UserError(_('No interest to post — all amounts are zero.'))

        created = self.env['microfinance.loan.voucher']
        for line in lines:
            # Check before creating accrual — block duplicate date ranges
            existing = self.env['microfinance.loan.voucher'].search([
                ('voucher_type',       '=',  'loan_payment'),
                ('loan_party_id',      '=',  line.loan_party_id.id),
                ('state',              'in', ['draft', 'posted']),
                ('interest_from_date', '!=', False),
                ('interest_from_date', '<=', self.date_to),
                ('interest_to_date',   '>=', self.date_from),
            ], limit=1)
            if existing:
                raise UserError(
                    _('Interest has already been calculated for "%s" for the selected '
                      'period or an overlapping period.\n\n'
                      'Existing accrual: %s (%s to %s)\n\n'
                      'Please change the date range and try again.')
                    % (line.loan_party_id.partner_id.name,
                       existing.name,
                       existing.interest_from_date,
                       existing.interest_to_date)
                )

            voucher = self.env['microfinance.loan.voucher'].create({
                'voucher_type':       'loan_payment',
                'partner_id':         line.loan_party_id.partner_id.id,
                'date':               self.date_to,
                'amount':             line.interest_amount,
                'journal_id':         self.journal_id.id,
                'contra_account_id':  self.interest_income_account_id.id,
                'interest_from_date': self.date_from,
                'interest_to_date':   self.date_to,
                'opening_balance':    line.opening_balance,
                'closing_balance':    line.closing_balance,
                'days':               line.days,
                'interest_rate':      line.interest_rate,
                'is_compound':        line.is_compound,
                'note': _(
                    'Interest Accrual | %s | %s to %s | '
                    'Balance: %.2f | Rate: %.2f%% | Days: %d'
                ) % (
                    line.loan_party_id.partner_id.name,
                    self.date_from, self.date_to,
                    line.effective_balance,
                    line.interest_rate, line.days,
                ),
            })
            voucher.action_post()
            created |= voucher

        return {
            'type':      'ir.actions.act_window',
            'name':      _('Posted Interest Accruals'),
            'res_model': 'microfinance.loan.voucher',
            'view_mode': 'list,form',
            'domain':    [('id', 'in', created.ids)],
        }

    # Proceed to Interest Payment 
    def action_proceed_to_payment(self):
        """
        MOD-03: Opens interest payment page (interest_receive voucher form)
        with calculation data pre-filled. Works for a single-party wizard.
        """
        self.ensure_one()
        if not self.computed:
            raise UserError(_('Please compute interest first.'))

        lines = self.line_ids.filtered(lambda l: l.interest_amount > 0)
        if not lines:
            raise UserError(_('No interest to record — all amounts are zero.'))

        if len(lines) == 1:
            line = lines[0]
            ctx = {
                'default_voucher_type':       'interest_receive',
                'default_partner_id':          line.loan_party_id.partner_id.id,
                'default_amount':              line.interest_amount,
                'default_journal_id':          self.journal_id.id,
                'default_interest_from_date':  str(self.date_from),
                'default_interest_to_date':    str(self.date_to),
                'default_days':                line.days,
                'default_interest_rate':       line.interest_rate,
                'default_is_compound':         line.is_compound,
                'default_opening_balance':     line.opening_balance,
                'default_closing_balance':     line.closing_balance,
            }
        else:
            # open filtered list of interest_receive vouchers to create
            ctx = {
                'default_voucher_type':       'interest_receive',
                'default_interest_from_date':  str(self.date_from),
                'default_interest_to_date':    str(self.date_to),
            }

        return {
            'type':      'ir.actions.act_window',
            'name':      _('Record Interest Payment'),
            'res_model': 'microfinance.loan.voucher',
            'view_mode': 'form',
            'target':    'new',
            'context':   ctx,
        }


class MicrofinanceLoanInterestLine(models.TransientModel):

    _name = 'microfinance.loan.interest.line'
    _description = 'Interest Calculation Preview Line'

    wizard_id           = fields.Many2one('microfinance.loan.interest', ondelete='cascade')
    loan_party_id       = fields.Many2one('microfinance.loan.party', string='Party')
    opening_balance     = fields.Float(string='Opening Balance')
    closing_balance     = fields.Float(string='Closing Balance')
    unpaid_interest     = fields.Float(string='Unpaid Interest (Compound)')
    effective_balance   = fields.Float(string='Effective Balance')
    interest_rate       = fields.Float(string='Rate (%)')
    is_compound         = fields.Boolean(string='Compound')
    days                = fields.Integer(string='Days')
    interest_amount     = fields.Float(string='Interest Amount')

    per_lac_interest    = fields.Float(
        string='Per Lac Interest',
        help='Interest amount per 100,000 BDT for this period and rate.',
    )
    cumulative_interest = fields.Float(
        string='Cumulative Interest',
        help='Previous closing balance + current period interest.',
    )
