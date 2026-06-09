{
    'name': 'Microfinance Loan Management',
    'version': '19.0.6.0.0',
    'summary': 'Flexible + Installment Loans (EMI & Flat) with Partial Payment, Interest Reports, Colorful Dashboard',
    'author': 'Khetan Trading',
    'category': 'Accounting/Finance',
    'depends': ['accountant', 'base'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/loan_party_views.xml',
        'views/loan_voucher_views.xml',
        'views/loan_interest_views.xml',
        'views/loan_installment_views.xml',
        'views/loan_dashboard_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'microfinance_loan/static/src/css/dashboard.css',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
