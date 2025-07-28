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
    general_ledger_data = get_general_ledger_data(filters)

    data = []

    for row in general_ledger_data:
        if row.get('account') in ["'Opening'", "'افتتاحي'"]:
            data.append({"account": 'Opening', "outstanding_amount": row.get('balance', 0) or 0})
            break

    for row in general_ledger_data:
        if row.get('account') in ["'Opening'", "'افتتاحي'"]:
            continue

        voucher_type = row.get('voucher_type')
        voucher_no = row.get('voucher_no')

        if not voucher_no:
            continue

        customer_group = frappe.get_value('Customer', row.get('party'), 'customer_group') if row.get('party') else ''
        if filters.get('customer_group') and customer_group not in filters.get('customer_group'):
            continue


        data.append({
            "posting_date": row.get('posting_date'),
            "party_type": row.get('party_type'),
            "party": row.get('party'),
            "account": row.get('account'),
            "voucher_type": row.get('voucher_type'),
            "voucher_no": row.get('voucher_no'),
            "invoiced_amount": row.get('debit_in_account_currency'),
            "paid_amount": row.get('credit_in_account_currency'),
            "outstanding_amount": row.get('debit_in_account_currency') - row.get('credit_in_account_currency'),
            "currency": row.get('presentation_currency'),
            "customer_group": customer_group,
        })

    get_missing_fields(data)

    calculate_ageing_periods(data, filters)

    data = add_and_filter_data_by_sales_person(data, filters)

    if filters.get('group_by_customer'):
        data = group_data_by_customer(data)

    data = add_total_row(data)

    return data

def get_general_ledger_data(filters):
    accounts = [filters.get('party_account')] if filters.get('party_account') else get_accounts()

    general_ledger_filters = _dict({
        'company': filters.get('company'),
        'from_date': filters.get('from_date'), 
        'to_date': filters.get('to_date'), 
        'voucher_no': filters.get('voucher_no'), 
        'account': accounts, 
        'party_type': filters.get('party_type'),
        'party': filters.get('party'), 
        'categorize_by': 'Categorize by Voucher (Consolidated)', 
        'include_dimensions': 1, 
        'include_default_book_entries': 1
    })

    columns, data = general_ledger(general_ledger_filters)

    return data

def get_accounts():
    accounts = frappe.db.sql("""
        SELECT name
        FROM `tabAccount`
        WHERE 
            freeze_account = 'No'
            AND is_group = 0
            AND account_type = 'Receivable'
    """, as_dict=True)

    return [account.name for account in accounts]

def get_missing_fields(data):
    for row in data:
        voucher_type = row.get('voucher_type')
        voucher_no = row.get('voucher_no')

        if not voucher_no:
            continue 

        if voucher_type == 'Sales Invoice':
            due_date = frappe.get_value('Sales Invoice', voucher_no, 'due_date')

            row['due_date'] = due_date

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

def add_and_filter_data_by_sales_person(data, filters):
    sales_persons = filters.get('sales_person')

    filtered_data = []
    if data and len(data):
        filtered_data.append(data[0])

    for row in data:
        voucher_type = row.get('voucher_type')
        voucher_no = row.get('voucher_no')

        sales_person = None
        if voucher_type == 'Sales Invoice':
            sales_person = frappe.get_value('Sales Invoice', voucher_no, 'sales_person')
        elif voucher_type == 'Payment Entry':
            sales_person = frappe.get_value('Payment Entry', voucher_no, 'sales_person')
        elif voucher_type == 'Journal Entry':
            je_sales_persons = frappe.db.sql("""
                SELECT sales_person
                FROM `tabJournal Entry Account`
                WHERE parent = %s
            """, (voucher_no), as_dict=True)
            for je_sales_person in je_sales_persons:
                if je_sales_person.get('sales_person'):
                    sales_person = je_sales_person.get('sales_person')
                    break
        else:
            continue

        row['sales_person'] = sales_person
            
        if sales_persons and len(sales_persons) and sales_person not in sales_persons:
            continue

        filtered_data.append(row)

    return filtered_data

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
        {"label": "Sales Person", "fieldname": "sales_person", "fieldtype": "Link", "options": "Sales Person", "width": 180},
        {"label": "Receivable Account", "fieldname": "account", "fieldtype": "Link", "options": "Account", "width": 180},
        {"label": "Voucher Type", "fieldname": "voucher_type", "fieldtype": "Data", "width": 180},
        {"label": "Voucher No", "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 180},
        {"label": "Due Date", "fieldname": "due_date", "fieldtype": "Date", "width": 150},
        {"label": "Invoiced Amount", "fieldname": "invoiced_amount", "fieldtype": "Currency", "width": 150},
        {"label": "Paid Amount", "fieldname": "paid_amount", "fieldtype": "Currency", "width": 150},
        {"label": "Credit Note", "fieldname": "credit_note", "fieldtype": "Currency", "width": 120},
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