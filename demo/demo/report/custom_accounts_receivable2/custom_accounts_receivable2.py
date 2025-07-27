# Copyright (c) 2025, Ismail Akram and contributors
# For license information, please see license.txt

import frappe
from frappe import _dict
from frappe.utils import getdate, nowdate

from erpnext.accounts.report.general_ledger.general_ledger import execute as general_ledger

def execute(filters=None):
    columns = get_columns(filters) 
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
            from_date = filters.get('from_date')
            to_date = filters.get('to_date')

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
                            AND ret_si.posting_date BETWEEN %s AND %s
                    ) AS credit_note,
                    (
                        SELECT 
                            SUM(ret_si.grand_total - ret_si.outstanding_amount)
                        FROM `tabSales Invoice` ret_si
                        WHERE 
                            ret_si.docstatus = 1 
                            AND ret_si.is_return = 1 
                            AND ret_si.return_against = si.name
                            AND ret_si.posting_date BETWEEN %s AND %s
                    ) AS paid_credit_note,
                    c.customer_group
                FROM `tabSales Invoice` si
                JOIN `tabCustomer` c ON c.name = si.customer
                WHERE 
                    si.name = %s
                    AND si.docstatus = 1
                    AND si.outstanding_amount > 0
                    AND si.status != 'Paid'
            """, (from_date, to_date, from_date, to_date, voucher_no,), as_dict=True)

            if not sales_invoice or float(sales_invoice[0].get('outstanding_amount')) == 0.0:
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

            invoiced_amount = sales_invoice[0].get('grand_total')
            paid_amount = sales_invoice[0].get('paid_amount')
            outstanding_amount = sales_invoice[0].get('outstanding_amount', 0) or 0
            paid_credit_note = sales_invoice[0].get('paid_credit_note', 0) or 0
            credit_note = sales_invoice[0].get('credit_note', 0) or 0

            data.append({
                "posting_date": entry.get('posting_date'),
                "due_date": sales_invoice[0].get('due_date'),
                "party_type": entry.get('party_type'),
                "party": entry.get('party'),
                "account": entry.get('account'),
                "voucher_type": entry.get('voucher_type'),
                "voucher_no": entry.get('voucher_no'),
                "invoiced_amount": invoiced_amount,
                "paid_amount": paid_amount,
                "outstanding_amount": outstanding_amount,
                "credit_note": sales_invoice[0].get('credit_note') or 0,
                "paid_credit_note": sales_invoice[0].get('paid_credit_note') or 0,
                "currency": entry.get('currency'),
                'customer_group': customer_group,
            })

        # 📘 Handle generic Journal Entries
        else:
            con = ''
            if filters.get('party'): 
                parties = "', '".join(filters.get('party'))
                con += f" AND party IN ('{parties}')"

            journal_details = frappe.db.sql(f"""
                SELECT 
                    SUM(debit_in_account_currency) AS debit,
                    SUM(credit_in_account_currency) AS credit,
                    party_type,
                    party
                FROM `tabJournal Entry Account` jea
                WHERE
                    jea.parent = %s
                    AND jea.account = %s
                    {con}
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

    calculate_ageing_periods(data, filters)

    if filters.get('group_by_customer'):
        data = group_data_by_customer(data)

    data = add_total_row(data)

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

def calculate_ageing_periods(data, filters):
    report_date = nowdate()
    ageing_based_on = filters.get("ageing_based_on", "Due Date")

    # Get ageing buckets
    ageing_ranges = get_ageing_ranges(filters)
    ageing_ranges = sorted(ageing_ranges)
    bucket_count = len(ageing_ranges)

    for row in data:
        base_date_str = row.get("due_date") if ageing_based_on == "Due Date" and row.get('voucher_type') == 'Sales Invoice' else row.get("posting_date")
        base_date = getdate(base_date_str) if base_date_str else None
        
        age = (getdate(nowdate()) - getdate(base_date)).days if base_date else 0
        row["age"] = age

        # Reset ageing buckets
        for i in range(bucket_count + 1):
            row[f"range_{i}"] = 0.0

        amount = row.get("outstanding_amount", 0.0)

        # Assign to correct bucket
        last_range = -1
        for i, current_range in enumerate(ageing_ranges):
            if age <= current_range:
                row[f"range_{i}"] += amount
                break
            last_range = current_range
        else:
            # Age is greater than last range
            row[f"range_{bucket_count}"] += amount

def group_data_by_customer(data): 
    grouped_data = []

    grouped_data.append(data[0])
    
    party_dict = {}
    taken_dict = {}

    for row in data:
        party = row.get('party')

        if not party:
            continue

        if party not in party_dict:
            party_dict[party] = []

        party_dict[party].append(row)

    for row in data:
        party = row.get('party')

        if party in taken_dict:
            continue
        taken_dict[party] = True

        total_row = {
            "bold": 1,
            "party": party,
            "invoiced_amount": 0,
            "paid_amount": 0,
            "credit_note": 0,
            "paid_credit_note": 0,
            "outstanding_amount": 0,
            "range_0": 0,
            "range_1": 0,
            "range_2": 0,
            "range_3": 0,
            "range_4": 0,
        }

        for field in party_dict.get(party, []):
            grouped_data.append(field)

            total_row['invoiced_amount'] += field.get('invoiced_amount', 0) or 0
            total_row['paid_amount'] += field.get('paid_amount', 0) or 0
            total_row['credit_note'] += field.get('credit_note', 0) or 0
            total_row['paid_credit_note'] += field.get('paid_credit_note', 0) or 0
            total_row['outstanding_amount'] += field.get('outstanding_amount', 0) or 0
            total_row['range_0'] += field.get('range_0', 0) or 0
            total_row['range_1'] += field.get('range_1', 0) or 0
            total_row['range_2'] += field.get('range_2', 0) or 0
            total_row['range_3'] += field.get('range_3', 0) or 0
            total_row['range_4'] += field.get('range_4', 0) or 0

        grouped_data.append(total_row)
        grouped_data.append({})

    return grouped_data

def add_total_row(data):
    total_row = {
        "bold": 1,
        "party": "Total",
        "invoiced_amount": 0,
        "paid_amount": 0,
        "credit_note": 0,
        "paid_credit_note": 0,
        "outstanding_amount": 0,
        "range_0": 0,
        "range_1": 0,
        "range_2": 0,
        "range_3": 0,
        "range_4": 0,
    }

    for field in data:
        if field.get('bold'):
            continue

        total_row['invoiced_amount'] += field.get('invoiced_amount', 0) or 0
        total_row['paid_amount'] += field.get('paid_amount', 0) or 0
        total_row['credit_note'] += field.get('credit_note', 0) or 0
        total_row['paid_credit_note'] += field.get('paid_credit_note', 0) or 0
        total_row['outstanding_amount'] += field.get('outstanding_amount', 0) or 0
        total_row['range_0'] += field.get('range_0', 0) or 0
        total_row['range_1'] += field.get('range_1', 0) or 0
        total_row['range_2'] += field.get('range_2', 0) or 0
        total_row['range_3'] += field.get('range_3', 0) or 0
        total_row['range_4'] += field.get('range_4', 0) or 0

    data.append(total_row)

    return data

def get_columns(filters):
    ranges = get_ageing_ranges(filters)

    columns = [
        {"label": "Posting Date", "fieldname": "posting_date", "fieldtype": "Date", "width": 150},
        {"label": "Party Type", "fieldname": "party_type", "fieldtype": "Data", "width": 150},
        {"label": "Party", "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 180},
        {"label": "Receivable Account", "fieldname": "account", "fieldtype": "Link", "options": "Account", "width": 180},
        {"label": "Voucher Type", "fieldname": "voucher_type", "fieldtype": "Data", "width": 180},
        {"label": "Voucher No", "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 180},
        {"label": "Due Date", "fieldname": "due_date", "fieldtype": "Date", "width": 150},
        {"label": "Invoiced Amount", "fieldname": "invoiced_amount", "fieldtype": "Currency", "width": 150},
        {"label": "Paid Amount", "fieldname": "paid_amount", "fieldtype": "Currency", "width": 150},
        {"label": "Credit Note", "fieldname": "credit_note", "fieldtype": "Currency", "width": 120},
        {"label": "Paid Credit Note", "fieldname": "paid_credit_note", "fieldtype": "Currency", "width": 120},
        {"label": "Outstanding Amount", "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 150},
        {"label": "Age (Days)", "fieldname": "age", "fieldtype": "Int", "width": 120},
    ]

    # Add ageing columns dynamically
    last_range = -1
    for idx, age in enumerate(ranges):
        label = f"{last_range + 1}-{age}"
        columns.append({
            "label": f"{label}",
            "fieldname": f"range_{idx}",
            "fieldtype": "Currency",
            "width": 120
        })
        last_range = age

    # Add final "Above" range column
    columns.append({
        "label": f"{last_range}-Above",
        "fieldname": f"range_{len(ranges)}",
        "fieldtype": "Currency",
        "width": 120
    })

    # Remaining columns
    columns += [
        {"label": "Currency", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 120},
        {"label": "Customer Group", "fieldname": "customer_group", "fieldtype": "Link", "options": "Customer Group", "width": 150},
    ]

    return columns

def get_ageing_ranges(filters):
    default_range = [30, 60, 90, 120]
    raw = filters.get("range", "")
    
    try:
        ranges = [int(r.strip()) for r in raw.split(",")]
    except ValueError:
        frappe.msgprint("Invalid ageing range format. Using default: 30, 60, 90, 120.")
        return default_range

    if len(ranges) != len(set(ranges)):
        frappe.msgprint("Duplicate values found in ageing range. Using default: 30, 60, 90, 120.")
        return default_range

    if ranges != sorted(ranges):
        return sorted(ranges)

    return ranges