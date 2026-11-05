# v10 — 2020 SPAR Excel workbook import (non-destructive)

This patch adds a separate `workbook_reliability_records` SQLite table. **It does not overwrite or merge** any of the existing PDF-reviewed `reliability_parameters` or `study_extraction_rows` entries.

## Install (Fedora Fish)

1. Back up your persistent SQLite database before first import.
2. From the project root, extract the ZIP with `unzip -o ~/Downloads/nrc_v10_excel_import.zip`.
3. Ensure `openpyxl` is included in `requirements.txt` (add `openpyxl>=3.1,<4` if absent) and install dependencies.
4. Run `python -m py_compile workbook_reliability_import.py review_interface.py`.
5. Run locally and log into `/review`. Upload `ParameterEstimates2020.xlsx` using the existing upload form.
6. View `/review/workbook`, review warnings, and use `/api/review/workbook/export?status=approved` for manually approved workbook JSON. `?status=validated` exports rows that passed automated numerical checks, **not** human approval.

## Important distinctions

- **Component Reliability**: Beta probability per demand or Gamma failure rate per hour, exposure and component population preserved. Empty merged component headings are carried down from prior rows.
- **IE Data**: initiating-event frequencies **per reactor critical year**, not component failure rates.
- **Train UA**: unavailability probabilities, often source **Normal** distributions; negative tails are not clipped or interpreted as physical probabilities automatically. Check applicability and boundedness.
- **Special Events**: specialized event probabilities/rates; all require source review.
- **Gamma**: workbook alpha=shape, workbook beta=**rate**, exported `scale=1/beta`.
- The new workbook JSON export is separate from the existing AnyLogic export; do not assume the existing `/api/reliability/anylogic` endpoint includes workbook records yet.
- Import is idempotent for the same workbook SHA256 and worksheet row: previous decisions are retained on reupload.
- Source workbook SHA256, sheet, row and raw cells are retained for audit.

## Test results against supplied workbook

419 parsed data rows: 300 component, 49 initiating-event, 55 train-unavailability, 15 special-event. 358 passed automated checks; 61 need review. These checks do **not** establish plant-specific applicability or independently verify the workbook's published data.

## Known limitations

- Dedicated review page currently displays raw record details and supports approval/rejection, not editing corrected values. If values require correction, reject them rather than approving; add a correction workflow in the next iteration.
- The source URL identifies the INL parameter-estimate collection, not a unique immutable workbook download URL. The SHA256 pins the exact uploaded workbook bytes.
- Other workbook editions/layouts are not supported; sheet names and columns are specific to 2020 V1.
- The current importer is a separate dataset rather than a migration into the original reliability library. This is intentional to prevent overwriting approved records and conflating categories.
