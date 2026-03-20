# Demo Assets

This folder contains generated media used by the repository documentation.

Contents:

- `EventTracker-demo-web-generate.gif`: the main README demo animation.
- `EventTracker-demo-web-generate-no-search-actions.gif`: an alternate animation without the filter and search action frames.
- `screenshots/`: the captured PNG frames used to build the GIFs.

To refresh these assets, start the app locally and run:

```powershell
uv run --with pillow python .\scripts\generate_demo_assets.py
```

To also rebuild the alternate GIF:

```powershell
uv run --with pillow python .\scripts\generate_demo_assets.py --also-no-search-actions
```

The generator script lives in `scripts/generate_demo_assets.py` and writes its output back into this folder.