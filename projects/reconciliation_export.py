"""Customer-price-only CSV, with spreadsheet formula protection for text cells."""

import csv
from io import StringIO


def reconciliation_csv(project, report):
    output = StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output)

    def row(*values):
        # Leave Decimal values numeric, including negative credits. Treat all text
        # as text even when a project name or line description resembles a formula.
        writer.writerow(
            [
                (
                    "'" + value
                    if value
                    and (
                        value[0] in "\t\r\n"
                        or value.lstrip().startswith(("=", "+", "-", "@"))
                    )
                    else value
                )
                if isinstance(value, str)
                else value
                for value in values
            ]
        )

    row("Allowances & project totals", project.name)
    row(
        "Customer prices only. Working project price; payments and applied credit memos are not deducted."
    )
    row(
        "Review amounts also included in manually entered change orders before final billing."
    )
    for warning in report.get("review_warnings", []):
        row("Review needed", warning)
    for title, entries, total in [
        ("Approved change orders", report["approved_orders"], report["approved_total"]),
        (
            "Not yet approved - excluded from new total",
            report["pending_orders"],
            report["pending_total"],
        ),
    ]:
        row()
        row(title)
        row(
            "Change order",
            "Title",
            "Status",
            "Line item",
            "Quantity",
            "Unit price",
            "Customer price",
        )
        for entry in entries:
            order = entry["order"]
            for line in entry["lines"]:
                row(
                    order.display_number,
                    order.title,
                    order.get_status_display(),
                    line["description"],
                    line["quantity"],
                    line["unit_price"],
                    line["price"],
                )
            row(order.display_number, "Order total", "", "", "", "", entry["total"])
        row(title + " total", total)
    row()
    row(
        "Selection section",
        "Selected price",
        "Allowance",
        "Over / under",
        "Already in change orders",
        "Net adjustment",
    )
    for section in report["sections"]:
        row(
            section["label"],
            section["price"],
            section["allowance"],
            section["variance"],
            section["in_change_orders"],
            section["net"],
        )
    row(
        "Selection totals",
        report["selection_price"],
        report["selection_allowance"],
        "",
        report["linked_total"],
        report["selection_total"],
    )
    row()
    row(
        "Supporting selections",
        "Selection",
        "Selected price",
        "Allowance",
        "Over / under",
        "Already in change orders",
        "Net adjustment",
        "Linked change orders",
    )
    for section in report["sections"]:
        for item in section["details"]:
            row(
                section["label"],
                item["selection"].title,
                item["price"],
                item["allowance"],
                item["variance"],
                item["covered"],
                item["net"],
                ", ".join(order.display_number for order in item["orders"]),
            )
    row(
        "Unpriced selections - excluded from savings",
        report["unpriced_count"],
        "Unpriced allowance",
        report["unpriced_allowance"],
    )
    row()
    row(
        "Original contract price",
        report["original_price"] if report["original_price"] is not None else "Not set",
    )
    row("Net adjustment", report["adjustment"])
    row(
        "New project total",
        report["new_total"]
        if report["new_total"] is not None
        else "Original price needed",
    )
    return output.getvalue()
