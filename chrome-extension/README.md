# Chrome bridge

Load this directory once as an unpacked extension:

1. Run `uv run socials install-chrome-extension`.
2. In `chrome://extensions`, enable **Developer mode**.
3. Click **Load unpacked** and select this `chrome-extension` directory.
4. Pin **AiFa Social** if you want its toolbar shortcut.

The extension has no cookie permission and no access to the normal Chrome
profile data. Its content script is limited to LinkedIn's Saved Posts URL and
activates only when the local dashboard supplies a random job ID in the URL
fragment. It posts extracted result-card text only to `127.0.0.1:8766`.
