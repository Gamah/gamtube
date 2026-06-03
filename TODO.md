# TODO

## Video page overlay

Add a dismissible overlay to `video.html` that appears briefly on load, then fades or slides out of the way so the video plays unobstructed.

Overlay should contain:

- **Share link** — the current page URL (`window.location.href`) in a click-to-copy field or button. No JS framework; `navigator.clipboard.writeText()` is fine.
- **About blurb** — one or two sentences explaining what the site is (paste any URL from YouTube, Instagram, TikTok, etc. and get a clean shareable link back).
- **Buy Me a Coffee link** — `https://buymeacoffee.com/gamah`

### Notes

- No new template — everything goes in `video.html` inline.
- Keep it minimal: no external CSS/JS, no dependencies, consistent with the rest of the site's no-chrome aesthetic.
- The overlay should not block the video from starting to load in the background.
- Auto-dismiss after a few seconds or on first click/tap anywhere is fine; a close button also works.
