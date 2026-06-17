# Test Data for the Agentic Billing Review Solution

This folder contains a curated working test dataset for the Python billing review prototype. It is organized to mirror the input structure expected by the solution
and to support contract-rule validation, transaction reconciliation, backup document matching, and exception handling.

## Structure

- `sample-inputs/contracts/` — contract rules and billing terms
- `sample-inputs/transactions/` — raw SAP unbilled transaction extract
- `sample-inputs/sap-outputs/` — draft invoice and timecard exports
- `sample-inputs/documents/` — synthetic backup receipts, invoices, and logs
- `sample-inputs/pm-instructions/` — Project Lead instruction emails
- `sample-inputs/prior-exceptions/` — historical resolution patterns

## Usage

Use these files as the input source for the prototype engine. The new `test-data` copy preserves the same structure as `appendix-sample-data` so the solution can be pointed at either location.
