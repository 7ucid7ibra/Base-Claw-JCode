# German Kokoro Assets

This folder is for optional local German Kokoro model assets.

Expected layout:

```text
german_kokoro/
  config.json
  kikiri_german_martin_ep10.pth
  voices/
    martin.pt
    victoria.pt
```

When these files are present, the server exposes German voices such as `dm_martin` and `dm_victoria` with `lang_code: "d"`.

Large model and voice files are intentionally ignored by git.
