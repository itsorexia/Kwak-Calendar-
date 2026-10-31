# Photo Grid Designer

A Streamlit app for arranging photos on a 7 × 5 grid and exporting a 6 × 4 inch print at 600 DPI.

## Deploy on Streamlit Community Cloud

1. Create a GitHub repository (for example, `photo-grid-designer`).
2. Upload all files in this folder to the repository root.
3. Open [Streamlit Community Cloud](https://share.streamlit.io/) and sign in with GitHub.
4. Choose **Create app**, then select the repository, branch (`main`) and entrypoint `app.py`.
5. Deploy. The app will receive a `*.streamlit.app` URL. Commits pushed to the selected branch update the app.

`requirements.txt` installs the Python packages. `packages.txt` installs Noto CJK fonts so Korean text can be rendered into PNG/PDF exports. HEIC/HEIF support is included through `pillow-heif`.

## Run locally

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Notes

- Uploaded photos and project JSON are held in the app session. Download a project JSON to keep a portable copy; the JSON includes image data.
- The web interface uses upload and selection controls in place of desktop file drag-and-drop.
- When printing the PDF, choose actual size or 100% scale to preserve the intended 6 × 4 inch page size.
