# Custom Voice Assets

Place compatible Kokoro community voice files here.

Example:

```text
voice_assets/custom/am_dylan.pt
```

If a file is named `am_dylan.pt`, call `/synthesize` with:

```json
{
  "voice": "am_dylan",
  "lang_code": "a"
}
```

Model and voice files are intentionally ignored by git.
