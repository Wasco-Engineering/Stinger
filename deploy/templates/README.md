# Config templates

Copy to machine-local stand directory via:

```powershell
.\scripts\deploy_init_stand.ps1 -StandId STINGER_02
```

Source files for the copy are repo-root `stinger_config.yaml` and `quality_cal_config.yaml` at init time. Edit the **local** copies under `%LOCALAPPDATA%\Stinger\<STAND_ID>\`.

Do not store production DB passwords in Z: shared folders.
