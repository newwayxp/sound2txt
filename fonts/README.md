# UI Fonts (downloaded, not bundled)

These fonts are **not** committed to this repository or shipped inside the
installer. They are fetched at install time by [`download_fonts.py`](../download_fonts.py).

Both are licensed under the **SIL Open Font License 1.1**:

| Font | Source |
|------|--------|
| Share Tech Mono | https://github.com/google/fonts/tree/main/ofl/sharetechmono |
| JetBrains Mono | https://github.com/JetBrains/JetBrainsMono |

To populate this directory manually for local development:

```bash
python download_fonts.py
```

The application falls back to a default monospace font if these are missing,
so the download is non-fatal.
