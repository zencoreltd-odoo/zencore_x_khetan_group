from odoo import models, fields, api, _
from odoo.exceptions import UserError
import math
from datetime import timedelta


class MicrofinanceLoanInstallment(models.Model):

    _name = 'microfinance.loan.installment'
    _description = 'Installment Loan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'

    name = fields.Char(
        string='Loan Reference',
        default=lambda self: _('New'),
        copy=False, readonly=True,
    )

    state = fields.Selection([
        ('draft',     'Draft'),
        ('running',   'Running'),
        ('closed',    'Closed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True, copy=False)

    loan_party_id = fields.Many2one(
        'microfinance.loan.party',
        string='Loan Party',
        required=True,
        domain="[('loan_account_id','!=',False)]",
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner', related='loan_party_id.partner_id',
        string='Customer', store=True,
    )
    loan_account_id = fields.Many2one(
        'account.account',
        related='loan_party_id.loan_account_id',
        string='Loan Account', store=True, readonly=True,
    )

    loan_type = fields.Selection([
        ('emi',  'EMI — Reducing Balance (Fixed Payment)'),
        ('flat', 'Flat — Equal Principal (Decreasing Payment)'),
    ], string='Loan Type', required=True, default='emi', tracking=True)

    principal_amount = fields.Monetary(
        string='Principal Amount', required=True,
        currency_field='currency_id', tracking=True,
    )
    annual_interest_rate = fields.Float(
        string='Annual Interest Rate (%)', required=True, tracking=True,
    )
    frequency = fields.Selection([
        ('daily',   'Daily'),
        ('weekly',  'Weekly'),
        ('monthly', 'Monthly'),
    ], string='Frequency', required=True, default='monthly', tracking=True)

    duration = fields.Integer(
        string='Duration (periods)', required=True, tracking=True,
        help='Total number of installments',
    )
    date_start = fields.Date(
        string='Start Date', required=True,
        default=fields.Date.context_today, tracking=True,
    )
    date_end = fields.Date(
        string='End Date', compute='_compute_date_end', store=True,
    )

    installment_amount = fields.Monetary(
        string='Installment Amount',
        compute='_compute_summary', store=True,
        currency_field='currency_id',
    )
    total_interest = fields.Monetary(
        string='Total Interest', compute='_compute_summary',
        store=True, currency_field='currency_id',
    )
    total_payment = fields.Monetary(
        string='Total Payment', compute='_compute_summary',
        store=True, currency_field='currency_id',
    )
    outstanding_balance = fields.Monetary(
        string='Outstanding Balance',
        compute='_compute_outstanding', store=False,
        currency_field='currency_id',
    )
    total_paid_principal = fields.Monetary(
        string='Paid Principal',
        compute='_compute_outstanding', store=False,
        currency_field='currency_id',
    )
    total_paid_interest = fields.Monetary(
        string='Paid Interest',
        compute='_compute_outstanding', store=False,
        currency_field='currency_id',
    )

    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id,
    )
    journal_id = fields.Many2one(
        'account.journal', string='Journal', required=True,
        domain="[('type','in',['bank','cash','general'])]",
    )
    disbursement_account_id = fields.Many2one(
        'account.account', string='Cash/Bank Account',
        required=True,
    )
    interest_income_account_id = fields.Many2one(
        'account.account', string='Interest Income Account', required=True,
    )

    line_ids = fields.One2many(
        'microfinance.loan.installment.line', 'loan_id',
        string='Installment Schedule',
    )
    line_count = fields.Integer(compute='_compute_counts', string='Total')
    paid_count  = fields.Integer(compute='_compute_counts', string='Paid')

    notes = fields.Text(string='Notes')

    #  Helpers 
    def _ppy(self):
        return {'daily': 365, 'weekly': 52, 'monthly': 12}[self.frequency]

    def _period_days(self):
        return {'daily': 1, 'weekly': 7, 'monthly': 30}[self.frequency]

    def _calc_emi(self, principal, annual_rate, n, freq):
        ppy = {'daily': 365, 'weekly': 52, 'monthly': 12}[freq]
        r = annual_rate / 100.0 / ppy
        if r == 0 or n == 0:
            return round(principal / n, 2) if n else 0
        emi = principal * r * math.pow(1+r, n) / (math.pow(1+r, n) - 1)
        return round(emi, 2)

    #  Computes 
    @api.depends('principal_amount', 'annual_interest_rate', 'duration', 'frequency', 'loan_type')
    def _compute_summary(self):
        for rec in self:
            P = rec.principal_amount or 0
            r_annual = rec.annual_interest_rate or 0
            n = rec.duration or 0
            if not (P and r_annual and n):
                rec.installment_amount = rec.total_interest = rec.total_payment = 0
                continue
            ppy = rec._ppy()
            r = r_annual / 100.0 / ppy
            if rec.loan_type == 'emi':
                emi = rec._calc_emi(P, r_annual, n, rec.frequency)
                rec.installment_amount = emi
                rec.total_payment      = round(emi * n, 2)
                rec.total_interest     = round(rec.total_payment - P, 2)
            else:
                principal_per = round(P / n, 2)
                first_interest = round(P * r, 2)
                rec.installment_amount = principal_per + first_interest
                total_int = 0.0
                bal = P
                for _ in range(n):
                    total_int += round(bal * r, 2)
                    bal = round(bal - principal_per, 2)
                rec.total_interest = round(total_int, 2)
                rec.total_payment  = round(P + total_int, 2)

    @api.depends('date_start', 'duration', 'frequency')
    def _compute_date_end(self):
        for rec in self:
            if rec.date_start and rec.duration:
                rec.date_end = rec.date_start + timedelta(
                    days=rec.duration * rec._period_days())
            else:
                rec.date_end = False

    @api.depends('line_ids', 'line_ids.state')
    def _compute_counts(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.paid_count = len(rec.line_ids.filtered(
                lambda l: l.state == 'paid'))

    @api.depends('line_ids.state', 'line_ids.principal',
                 'line_ids.interest', 'line_ids.paid_amount')
    def _compute_outstanding(self):
        for rec in self:
            paid    = rec.line_ids.filtered(lambda l: l.state == 'paid')
            partial = rec.line_ids.filtered(lambda l: l.state == 'partial')
            pp = sum(paid.mapped('principal'))
            pi = sum(paid.mapped('interest'))
            pp += sum(l.paid_amount - min(l.paid_amount, l.interest) for l in partial)
            pi += sum(min(l.paid_amount, l.interest) for l in partial)
            rec.total_paid_principal = round(pp, 2)
            rec.total_paid_interest  = round(pi, 2)
            rec.outstanding_balance  = round(rec.principal_amount - pp, 2)

    #  One-at-a-time installment creation 
    def _create_next_installment(self, sequence, balance, carry_interest=0.0):
        
        self.ensure_one()
        if sequence > self.duration:
            return  # All periods exhausted

        ppy         = self._ppy()
        period_days = self._period_days()
        r           = self.annual_interest_rate / 100.0 / ppy
        remaining_n = self.duration - sequence + 1  

        # Due date
        if sequence == 1:
            due_date = self.date_start
        else:
            prev = self.line_ids.filtered(lambda l: l.sequence == sequence - 1)
            due_date = (prev.date_end + timedelta(days=1)) if prev else self.date_start

        period_end = due_date + timedelta(days=period_days - 1)
        interest   = round(balance * r, 2)

        if self.loan_type == 'emi':
            if r > 0 and remaining_n > 0:
                emi = balance * r * math.pow(1+r, remaining_n) / (math.pow(1+r, remaining_n) - 1)
                emi = round(emi, 2)
                principal = round(emi - interest, 2)
            else:
                principal = round(balance / remaining_n, 2) if remaining_n else balance
                emi = round(principal + interest, 2)
            if remaining_n == 1:         
                principal = round(balance, 2)
                emi = round(principal + interest, 2)
            due = round(emi + carry_interest, 2)
        else:
            principal = round(balance / remaining_n, 2) if remaining_n > 0 else balance
            if remaining_n == 1:
                principal = round(balance, 2)
            due = round(principal + interest + carry_interest, 2)

        new_balance = max(0.0, round(balance - principal, 2))

        self.env['microfinance.loan.installment.line'].create({
            'loan_id':             self.id,
            'sequence':            sequence,
            'date':                due_date,
            'date_end':            period_end,
            'principal':           principal,
            'interest':            interest,
            'emi_amount':          due,
            'outstanding_balance': new_balance,
            'state':               'draft',
        })

    #  Confirm & Disburse 
    def action_confirm(self):
        """
        Confirm loan:
          1. Post disbursement voucher
          2. Create ONLY the 1st installment (requirement 1)
        """
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_('Only draft loans can be confirmed.'))
        if not (self.principal_amount and self.duration and self.annual_interest_rate):
            raise UserError(_('Fill in Principal, Duration, and Interest Rate first.'))

        voucher = self.env['microfinance.loan.voucher'].create({
            'voucher_type':      'loan_payment',
            'partner_id':        self.partner_id.id,
            'date':              self.date_start,
            'amount':            self.principal_amount,
            'journal_id':        self.journal_id.id,
            'contra_account_id': self.disbursement_account_id.id,
            'note': _('Loan disbursement: %s') % self.name,
        })
        voucher.action_post()

        # First installment only
        self._create_next_installment(sequence=1, balance=self.principal_amount)

        self.write({'state': 'running'})
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   _('Loan Confirmed & Disbursed'),
                'message': _('Voucher %s posted. Installment #1 created.') % voucher.name,
                'type': 'success', 'sticky': False,
            },
        }

    def action_close(self):
        self.ensure_one()
        unpaid = self.line_ids.filtered(lambda l: l.state not in ('paid',))
        if unpaid:
            raise UserError(_('%d installments still unpaid/partial.') % len(unpaid))
        self.state = 'closed'

    def action_cancel(self):
        self.ensure_one()
        self.state = 'cancelled'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = (self.env['ir.sequence'].next_by_code(
                    'microfinance.loan.installment') or _('New'))
        return super().create(vals_list)


# Installment Schedule Line

class MicrofinanceLoanInstallmentLine(models.Model):

    _name = 'microfinance.loan.installment.line'
    _description = 'Installment Schedule Line'
    _order = 'loan_id, sequence'

    loan_id   = fields.Many2one('microfinance.loan.installment',
                                string='Loan', required=True, ondelete='cascade')
    sequence  = fields.Integer(string='#', default=1)

    date      = fields.Date(string='Due Date',   required=True)
    date_end  = fields.Date(string='Period End')
    date_paid = fields.Date(string='Paid Date', readonly=True)

    principal  = fields.Monetary(string='Principal', currency_field='currency_id')
    interest   = fields.Monetary(string='Interest',  currency_field='currency_id')
    emi_amount = fields.Monetary(string='Due Amount', currency_field='currency_id')

    outstanding_balance = fields.Monetary(string='Balance After',
                                          currency_field='currency_id')
    currency_id = fields.Many2one(related='loan_id.currency_id', store=True)

    state = fields.Selection([
        ('draft',   'Unpaid'),
        ('partial', 'Partial'),
        ('paid',    'Paid'),
        ('overdue', 'Overdue'),
    ], default='draft')

    paid_amount   = fields.Monetary(string='Paid Amount',
                                    currency_field='currency_id', readonly=True)
    early_payment = fields.Boolean(string='Early Pay', readonly=True)

    principal_voucher_id = fields.Many2one(
        'microfinance.loan.voucher', string='Repayment Voucher', readonly=True)
    interest_voucher_id  = fields.Many2one(
        'microfinance.loan.voucher', string='Interest Voucher', readonly=True)

    notes = fields.Char(string='Notes')

    #  Requirement 5: Readonly guard on paid lines 
    _SYSTEM_KEYS = {
        'state', 'date_paid', 'paid_amount', 'early_payment',
        'principal_voucher_id', 'interest_voucher_id',
        '__last_update', 'message_ids', 'activity_ids',
    }

    def write(self, vals):
        for rec in self:
            if rec.state == 'paid' and not set(vals.keys()).issubset(self._SYSTEM_KEYS):
                raise UserError(
                    _('Installment #%d is already paid and cannot be modified.')
                    % rec.sequence)
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state == 'paid':
                raise UserError(
                    _('Cannot delete paid installment #%d.') % rec.sequence)
        return super().unlink()

    #  Requirement  
    def action_pay(self):
        """Open payment wizard with full remaining amount pre-filled."""
        self.ensure_one()
        if self.state == 'paid':
            raise UserError(_('Already paid.'))
        remaining = max(0.0, round(self.emi_amount - self.paid_amount, 2))
        return {
            'type':      'ir.actions.act_window',
            'name':      _('Pay Installment #%d') % self.sequence,
            'res_model': 'microfinance.loan.partial.payment',
            'view_mode': 'form',
            'target':    'new',
            'context': {
                'default_line_id':     self.id,
                'default_due_amount':  self.emi_amount,
                'default_paid_so_far': self.paid_amount,
                'default_paid_amount': remaining,  
            },
        }

    def action_pay_partial(self):
        """Open payment wizard with empty amount (user enters partial)."""
        self.ensure_one()
        if self.state == 'paid':
            raise UserError(_('Already fully paid.'))
        return {
            'type':      'ir.actions.act_window',
            'name':      _('Partial Payment — Installment #%d') % self.sequence,
            'res_model': 'microfinance.loan.partial.payment',
            'view_mode': 'form',
            'target':    'new',
            'context': {
                'default_line_id':     self.id,
                'default_due_amount':  self.emi_amount,
                'default_paid_so_far': self.paid_amount,
            },
        }

    #  Requirement  create next installment 
    def _do_full_payment(self, payment_date=None):
        """
        Mark as paid, post vouchers, then automatically create the
        NEXT installment based on remaining outstanding balance.
        """
        self.ensure_one()
        loan  = self.loan_id
        today = payment_date or fields.Date.today()

        p_voucher, i_voucher = self._create_payment_vouchers(
            loan, today, self.principal, self.interest, self.emi_amount)

        # Use super() to bypass the paid-guard we added in write()
        super(MicrofinanceLoanInstallmentLine, self).write({
            'state':                'paid',
            'date_paid':            today,
            'paid_amount':          self.emi_amount,
            'early_payment':        today < self.date,
            'principal_voucher_id': p_voucher.id if p_voucher else False,
            'interest_voucher_id':  i_voucher.id if i_voucher else False,
        })

        #  Create next installment 
        loan._create_next_installment(
            sequence=self.sequence + 1,
            balance=self.outstanding_balance,
        )

        if self.sequence >= loan.duration:
            loan.state = 'closed'

        return self._notify(_('Installment #%d fully paid. Next installment created.') % self.sequence)

    def _create_payment_vouchers(self, loan, date, principal, interest, total_paid):
        if total_paid <= 0:
            return self.env['microfinance.loan.voucher'], self.env['microfinance.loan.voucher']

        interest_paid  = min(total_paid, interest)
        principal_paid = round(total_paid - interest_paid, 2)
        interest_paid  = round(interest_paid, 2)

        p_voucher = self.env['microfinance.loan.voucher']
        i_voucher = self.env['microfinance.loan.voucher']

        if principal_paid > 0:
            p_voucher = self.env['microfinance.loan.voucher'].create({
                'voucher_type':        'loan_receive',
                'partner_id':          loan.partner_id.id,
                'date':                date,
                'amount':              principal_paid,
                'journal_id':          loan.journal_id.id,
                'contra_account_id':   loan.disbursement_account_id.id,
                'installment_line_id': self.id,
                'note': _('Installment #%d principal — %s') % (self.sequence, loan.name),
            })
            p_voucher.with_context(skip_installment_link=True).action_post()

        if interest_paid > 0:
            i_voucher = self.env['microfinance.loan.voucher'].create({
                'voucher_type':        'interest_receive',
                'partner_id':          loan.partner_id.id,
                'date':                date,
                'amount':              interest_paid,
                'journal_id':          loan.journal_id.id,
                'contra_account_id':   loan.disbursement_account_id.id,
                'installment_line_id': self.id,
                'note': _('Installment #%d interest — %s') % (self.sequence, loan.name),
            })
            i_voucher.with_context(skip_installment_link=True).action_post()

        return p_voucher, i_voucher

    def _apply_partial_payment(self, paid_amount, payment_date=None):
        self.ensure_one()
        loan  = self.loan_id
        today = payment_date or fields.Date.today()

        p_voucher, i_voucher = self._create_payment_vouchers(
            loan, today, self.principal, self.interest, paid_amount)

        shortfall = round(self.emi_amount - paid_amount, 2)

        super(MicrofinanceLoanInstallmentLine, self).write({
            'state':                'partial',
            'date_paid':            today,
            'paid_amount':          paid_amount,
            'early_payment':        today < self.date,
            'principal_voucher_id': p_voucher.id if p_voucher else False,
            'interest_voucher_id':  i_voucher.id if i_voucher else False,
        })

        if shortfall > 0:
            self._recalculate_remaining(shortfall)

    def _recalculate_remaining(self, shortfall=0.0):
        loan = self.loan_id
        remaining = loan.line_ids.filtered(
            lambda l: l.state == 'draft' and l.sequence > self.sequence
        ).sorted('sequence')

        if not remaining:
            return

        interest_paid    = min(self.paid_amount, self.interest)
        principal_paid   = round(self.paid_amount - interest_paid, 2)
        unpaid_principal = round(self.principal - principal_paid, 2)
        new_balance      = round(self.outstanding_balance + unpaid_principal, 2)

        n   = len(remaining)
        ppy = loan._ppy()
        r   = loan.annual_interest_rate / 100.0 / ppy

        if loan.loan_type == 'emi':
            if r > 0 and n > 0:
                new_emi = new_balance * r * math.pow(1+r, n) / (math.pow(1+r, n) - 1)
                new_emi = round(new_emi, 2)
            else:
                new_emi = round(new_balance / n, 2) if n else 0
        else:
            principal_per = round(new_balance / n, 2)

        balance = new_balance
        add_shortfall = shortfall

        for i, line in enumerate(remaining):
            interest = round(balance * r, 2)
            if loan.loan_type == 'emi':
                principal = round(new_emi - interest, 2)
                due = new_emi
                if i == len(remaining) - 1:
                    principal = round(balance, 2)
                    due = round(principal + interest, 2)
            else:
                principal = principal_per
                if i == len(remaining) - 1:
                    principal = round(balance, 2)
                due = round(principal + interest, 2)

            due_with_carry = round(due + add_shortfall, 2)
            add_shortfall = 0.0

            balance = round(balance - principal, 2)
            if balance < 0:
                balance = 0.0

            line.write({
                'principal':           principal,
                'interest':            interest,
                'emi_amount':          due_with_carry,
                'outstanding_balance': balance,
            })

    #  Link from interest/repayment wizard 
    def _check_and_mark_paid_from_vouchers(self):
        
        self.ensure_one()
        if self.state == 'paid':
            return

        p_amt = self.principal_voucher_id.amount if self.principal_voucher_id else 0.0
        i_amt = self.interest_voucher_id.amount  if self.interest_voucher_id  else 0.0
        total_paid = round(p_amt + i_amt, 2)
        today = fields.Date.today()

        if total_paid <= 0:
            return

        if total_paid >= self.emi_amount:
            super(MicrofinanceLoanInstallmentLine, self).write({
                'state':       'paid',
                'date_paid':   today,
                'paid_amount': total_paid,
            })
            self.loan_id._create_next_installment(
                sequence=self.sequence + 1,
                balance=self.outstanding_balance,
            )
            if self.sequence >= self.loan_id.duration:
                self.loan_id.state = 'closed'
        else:
            shortfall = round(self.emi_amount - total_paid, 2)
            super(MicrofinanceLoanInstallmentLine, self).write({
                'state':       'partial',
                'date_paid':   today,
                'paid_amount': total_paid,
            })
            if shortfall > 0:
                self._recalculate_remaining(shortfall)

    def action_view_vouchers(self):
        self.ensure_one()
        voucher_ids = [v.id for v in [self.principal_voucher_id, self.interest_voucher_id] if v]
        return {
            'type':      'ir.actions.act_window',
            'name':      _('Payment Vouchers'),
            'res_model': 'microfinance.loan.voucher',
            'view_mode': 'list,form',
            'domain':    [('id', 'in', voucher_ids)],
        }

    def _notify(self, msg):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _('Done'), 'message': msg,
                       'type': 'success', 'sticky': False},
        }


# Unified Payment Wizard  

class MicrofinanceLoanPartialPayment(models.TransientModel):

    _name = 'microfinance.loan.partial.payment'
    _description = 'Installment Payment Wizard'

    line_id     = fields.Many2one('microfinance.loan.installment.line',
                                  string='Installment', required=True)
    due_amount  = fields.Monetary(string='Total Due',    currency_field='currency_id',
                                  readonly=True)
    paid_so_far = fields.Monetary(string='Already Paid', currency_field='currency_id',
                                  readonly=True)
    remaining_due = fields.Monetary(string='Remaining Due', currency_field='currency_id',
                                    compute='_compute_remaining', readonly=True)
    paid_amount = fields.Monetary(string='Paying Now',   currency_field='currency_id',
                                  required=True)
    currency_id = fields.Many2one('res.currency',
                                  default=lambda self: self.env.company.currency_id)
    shortfall   = fields.Monetary(string='Shortfall (carry to next)',
                                  compute='_compute_shortfall',
                                  currency_field='currency_id')
    payment_date = fields.Date(
        string='Payment Date',
        default=fields.Date.context_today,
        required=True,
    )

    @api.depends('due_amount', 'paid_so_far')
    def _compute_remaining(self):
        for rec in self:
            rec.remaining_due = max(0.0, round(rec.due_amount - rec.paid_so_far, 2))

    @api.depends('due_amount', 'paid_amount', 'paid_so_far')
    def _compute_shortfall(self):
        for rec in self:
            remaining_due = rec.due_amount - rec.paid_so_far
            rec.shortfall = max(0.0, round(remaining_due - rec.paid_amount, 2))

    @api.constrains('paid_amount')
    def _check_amount(self):
        for rec in self:
            remaining = rec.due_amount - rec.paid_so_far
            if rec.paid_amount <= 0:
                raise UserError(_('Amount must be greater than zero.'))
            if rec.paid_amount > remaining:
                raise UserError(
                    _('Cannot pay more than remaining due (%.2f).') % remaining)

    def action_confirm(self):
        self.ensure_one()
        remaining = round(self.due_amount - self.paid_so_far, 2)
        if round(self.paid_amount, 2) >= remaining:
            self.line_id._do_full_payment(payment_date=self.payment_date)
        else:
            self.line_id._apply_partial_payment(
                self.paid_amount, payment_date=self.payment_date)
        return {'type': 'ir.actions.act_window_close'}
