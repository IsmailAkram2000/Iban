# Copyright (c) 2025, Ismail Akram and contributors
# For license information, please see license.txt

import frappe
from frappe import _dict

from erpnext.accounts.report.general_ledger.general_ledger import execute as general_ledger

def execute(filters=None):
    columns = get_columns() 
    data = get_data(filters)

    return columns, data

def get_data(filters):
    entries = get_gl_entries(filters)

    data = get_opening_balance(filters)

    voucher_no_dict = {}

    for entry in entries:    
        voucher_no = entry.get('voucher_no')

        if voucher_no in voucher_no_dict:
            continue
        voucher_no_dict[voucher_no] = True

        # 💳 Handle payments
        if entry.get('voucher_type') == 'Payment Entry':
            payment_details = frappe.db.sql(f"""
                SELECT 
                    pe.payment_type,
                    pe.paid_amount,
                    pe.party
                FROM `tabPayment Entry` pe
                WHERE 
                    pe.name = %s
                    AND pe.docstatus = 1
            """, (voucher_no,), as_dict=True)
            if not payment_details:
                continue

            paid_amount = payment_details[0].get('paid_amount', 0)

            customer_group = frappe.get_value('Customer', payment_details[0].get('party', ''), 'customer_group')

            if not paid_amount:
                continue

            if filters.get('party_type') == 'Customer' and filters.get('customer_group') and customer_group not in filters.get('customer_group'):
                continue

            payment_type = payment_details[0].get('payment_type', 0)
            if payment_type not in ['Pay', 'Receive']:
                continue

            data.append({
                "posting_date": entry.get('posting_date'),
                "party_type": 'Customer',
                "party": payment_details[0].get('party', ''),
                "account": entry.get('account'),
                "voucher_type": entry.get('voucher_type'),
                "voucher_no": entry.get('voucher_no'),
                "invoiced_amount": paid_amount if payment_type == 'Pay' else 0,
                "paid_amount": paid_amount if payment_type == 'Receive' else 0,
                "outstanding_amount": paid_amount if payment_type == 'Pay' else paid_amount * -1,
                "credit_note": 0,
                "currency": entry.get('currency'),
                'customer_group': customer_group,
            })
                    
        # 🧾 Handle sales invoice
        elif entry['voucher_type'] == 'Sales Invoice':
            sales_invoice = frappe.db.sql("""
                SELECT 
                    si.grand_total,
                    si.pos_profile,
                    si.sales_person,
                    si.due_date,
                    CASE 
                        WHEN si.status = 'Paid' THEN 0
                        ELSE si.outstanding_amount
                    END AS outstanding_amount,
                    si.grand_total - 
                        CASE 
                            WHEN si.status = 'Paid' THEN 0
                            ELSE si.outstanding_amount
                        END AS paid_amount,
                    (
                        SELECT 
                            SUM(ret_si.grand_total)
                        FROM `tabSales Invoice` ret_si
                        WHERE 
                            ret_si.docstatus = 1 
                            AND ret_si.is_return = 1 
                            AND ret_si.return_against = si.name
                    ) AS credit_note,
                    c.customer_group
                FROM `tabSales Invoice` si
                JOIN `tabCustomer` c ON c.name = si.customer
                WHERE 
                    si.name = %s
                    AND si.docstatus = 1
                    AND is_return = 0
            """, (voucher_no,), as_dict=True)

            if not sales_invoice:
                continue

            customer_group = sales_invoice[0].get('customer_group')
            pos_profile = sales_invoice[0].get('pos_profile')
            sales_person = sales_invoice[0].get('sales_person')

            if filters.get('party_type') == 'Customer' and filters.get('customer_group') and customer_group not in filters.get('customer_group'):
                continue
            if filters.get('pos_profile') and filters.get('pos_profile') != pos_profile:
                continue
            if filters.get('sales_person') and filters.get('sales_person') != sales_person:
                continue

            data.append({
                "posting_date": entry.get('posting_date'),
                "due_date": sales_invoice[0].get('due_date'),
                "party_type": entry.get('party_type'),
                "party": entry.get('party'),
                "account": entry.get('account'),
                "voucher_type": entry.get('voucher_type'),
                "voucher_no": entry.get('voucher_no'),
                "invoiced_amount": sales_invoice[0].get('grand_total'),
                "paid_amount": sales_invoice[0].get('paid_amount'),
                "outstanding_amount": sales_invoice[0].get('outstanding_amount'),
                "credit_note": sales_invoice[0].get('credit_note') or 0,
                "currency": entry.get('currency'),
                'customer_group': customer_group,
            })

        # 📘 Handle generic Journal Entries
        else:
            journal_details = frappe.db.sql("""
                SELECT 
                    SUM(debit_in_account_currency) AS debit,
                    SUM(credit_in_account_currency) AS credit,
                    party_type,
                    party
                FROM `tabJournal Entry Account` jea
                WHERE
                    jea.parent = %s
                    AND jea.account = %s
                GROUP BY jea.account
            """, (voucher_no, entry.get('account')), as_dict=True)

            if not journal_details:
                continue

            debit = journal_details[0].get('debit')
            credit = journal_details[0].get('credit')
            party_type = journal_details[0].get('party_type')
            party = journal_details[0].get('party')

            customer_group = frappe.get_value('Customer', party, 'customer_group')
            if filters.get('party_type') == 'Customer' and filters.get('customer_group') and customer_group not in filters.get('customer_group'):
                continue

            data.append({
                "posting_date": entry.get('posting_date'),
                "party_type": party_type or entry.get('party_type'),
                "party": party or entry.get('party'),
                "account": entry.get('account'),
                "voucher_type": entry.get('voucher_type'),
                "voucher_no": entry.get('voucher_no'),
                "invoiced_amount": debit if debit > 0 else 0,
                "paid_amount": credit if credit > 0 else 0,
                "outstanding_amount": credit * -1 if credit > 0 else debit,
                "credit_note": 0,
                "currency": entry.get('currency'),
                'customer_group': customer_group,
            })

    return data

def get_opening_balance(filters):
    general_ledger_filters = _dict({
        'company': filters.get('company'),
        'from_date': filters.get('from_date'), 
        'to_date': '2025-07-24', 
        'account': filters.get('party_account'), 
        'party_type': filters.get('party_type'),
        'party': filters.get('party'), 
        'categorize_by': 'Categorize by Voucher (Consolidated)', 
        'include_dimensions': 1, 
        'include_default_book_entries': 1
    })

    columns, data = general_ledger(general_ledger_filters)

    for row in data:
        if row.get('account') in ["'Opening'", "'افتتاحي'"]:
            return [{"account": 'Opening', "outstanding_amount": row.get('balance', 0) or 0}] 

    return []

def get_gl_entries(filters):
    con = ''
    if filters.get('company'):
        con += f" AND gl.company = '{filters.get('company')}'"
    if filters.get('from_date'):
        con += f" AND gl.posting_date >= '{filters.get('from_date')}'"
    if filters.get('to_date'):
        con += f" AND gl.posting_date <= '{filters.get('to_date')}'"
    if filters.get('voucher_type'):
        con += f" AND gl.voucher_type = '{filters.get('voucher_type')}'"
    if filters.get('voucher_no'):
        con += f" AND gl.voucher_no = '{filters.get('voucher_no')}'"
    if filters.get('party_type'):
        con += f" AND gl.party_type = '{filters.get('party_type')}'"
    if filters.get('party'):
        parties = "', '".join(filters.get('party'))
        con += f" AND gl.party IN ('{parties}')"
    if filters.get('party_account'):
        con += f" AND gl.account = '{filters.get('account')}'"
    if filters.get('sales_person') or filters.get('pos_profile'):
        con += f" AND gl.voucher_type = 'Sales Invoice'"

    entries = frappe.db.sql(f"""
        SELECT 
            gl.name,
            gl.posting_date,
            gl.account,
            gl.voucher_type,
            gl.voucher_no,
            gl.party_type,
            gl.party,
            gl.account_currency AS currency
        FROM `tabGL Entry` gl
        LEFT JOIN `tabAccount` a ON a.name = gl.account
        WHERE 
            gl.is_cancelled = 0
            AND a.account_type = 'Receivable'
            {con}
        ORDER BY gl.creation DESC
    """, as_dict=True)

    return entries

def get_columns():
    columns = [
        {
            "label": "Posting Date",
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width": 150,
        },
        {
            "label": "Party Type",
            "fieldname": "party_type",
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "label": "Party",
            "fieldname": "party",
            "fieldtype": "Dynamic Link",
            "options": "party_type",
            "width": 180,
        },
        {
            "label": "Receivable Account",
            "fieldname": "account",
            "fieldtype": "Link",
            "options": "Account",
            "width": 180,
        },
        {
            "label": "Voucher Type",
            "fieldname": "voucher_type",
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "label": "Voucher No",
            "fieldname": "voucher_no",
            "fieldtype": "Dynamic Link",
            "options": "voucher_type",
            "width": 180,
        },
        {
            "label": "Due Date",
            "fieldname": "due_date",
            "fieldtype": "Date",
            "width": 150,
        },
        {
            "label": "Invoiced Amount",
            "fieldname": "invoiced_amount",
            "fieldtype": "Currency",
            "width": 150,
        },
        {
            "label": "Paid Amount",
            "fieldname": "paid_amount",
            "fieldtype": "Currency",
            "width": 150,
        },
        {
            "label": "Credit Note",
            "fieldname": "credit_note",
            "fieldtype": "Currency",
            "width": 150,
        },
        {
            "label": "Outstanding Amount",
            "fieldname": "outstanding_amount",
            "fieldtype": "Currency",
            "width": 150,
        },
        {
            "label": "Currency",
            "fieldname": "currency",
            "fieldtype": "Link",
            "options": "Currency",
            "width": 120,
        },
        {
            "label": "Customer Group",
            "fieldname": "customer_group",
            "fieldtype": "Link",
            "options": "Customer Group",
            "width": 150,
        },
    ]

    return columns