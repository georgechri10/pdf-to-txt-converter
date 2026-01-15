# PDF to TXT Converter

Simple web app that converts Greek cadastral/topographic PDFs to coordinate TXT files.

## How to Use

1. Upload a ZIP file containing PDF files
2. Click "Download TXT files" to get the converted files

## Output Format

```
PointID,X,Y,0.00,KTHMA
```

## Deploy Your Own

[![Deploy to Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://share.streamlit.io/)

1. Fork this repo
2. Go to [share.streamlit.io](https://share.streamlit.io/)
3. Connect your GitHub and select this repo
4. Deploy!

## Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```
