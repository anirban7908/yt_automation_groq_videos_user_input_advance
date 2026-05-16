YOUTUBE AUTOMATION PROJECT - README FOLDER INDEX
===============================================

This folder documents the current project pipeline and active modules.

Recommended Reading Order
-------------------------
1. YouTube_Upload_Automation_Guide.txt
2. main_documentation.txt
3. scheduler_documentation.txt
4. db_manager.txt
5. scraper.txt
6. brain.txt
7. voice.txt
8. visuals.txt
9. assembler.txt
10. upload_prep.txt
11. thumbnail_gen.txt
12. uploader_documentation.txt
13. meta_uploader.txt

Support Modules
---------------
- ai_core.txt
- auth_check.txt

Active Pipeline
---------------
main.py coordinates this status flow:

pending -> scripted -> voiced -> ready_to_assemble -> ready_to_upload -> completed_packaged -> uploaded

Docs Removed During Cleanup
---------------------------
The following README files were removed because their content was stale or no longer matched an active pipeline file:
- verifier.txt
- fallback_mechanics_docs.txt
- retention_and_visuals_docs.txt

The useful fallback and visual-keyword notes were folded into visuals.txt and brain.txt.
