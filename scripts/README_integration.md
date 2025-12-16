ETL Integration: Clear sales caches after import

Purpose
- Ensure dashboards pick up newly imported sales data immediately by clearing sales-related caches after your ETL job completes.

Options
1) Call the management command (recommended)
   - Use the included helper scripts to run the `clear_sales_cache` management command in your project's virtualenv.

2) Call the management command from your ETL (preferred):
   - After your ETL finishes loading data into the DB, run:
     ```bash
     python /path/to/project/manage.py clear_sales_cache
     ```
   - This invokes `clear_registered_sales_caches()` if keys were registered; otherwise falls back to clearing `salesrecord_all_dates` and all caches.

3) Use the included shell/PowerShell wrapper
   - Linux/macOS example:
     ```bash
     ./scripts/clear_sales_cache_after_etl.sh /path/to/venv /path/to/project
     ```
   - Windows PowerShell example:
     ```powershell
     .\scripts\clear_sales_cache_after_etl.ps1 -VenvPath C:\path\to\venv -ProjectPath C:\path\to\project
     ```

Notes
- The app registers cache keys used by views via `register_sales_cache_key(key)`. The management command will clear those registered keys when present; if none are registered it clears the main date list and then clears all caches as a fallback.
- If your ETL runs many files in a loop, call the clear command once after the entire batch finishes.
- For CI/cron: add an entry that runs the wrapper script after upload completes.

Example cron (run nightly at 2:30):
```
30 2 * * * /home/deploy/.venv/bin/python /home/deploy/project/manage.py import_sales --file /data/incoming/today.xlsx && /home/deploy/project/scripts/clear_sales_cache_after_etl.sh /home/deploy/.venv /home/deploy/project
```

If you'd like, I can also:
- Implement a management command that runs your ETL file and clears caches in one step.
- Add a small CI job example for GitHub Actions or similar.
