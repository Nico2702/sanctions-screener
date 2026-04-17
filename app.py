import streamlit as st
import sys

st.title("NaroIX Diagnostic")
st.write("If you can see this, Streamlit and Python are working.")
st.write(f"Python version: {sys.version}")

st.divider()
st.subheader("Import test")

try:
    import pandas as pd
    st.success(f"pandas {pd.__version__} OK")
except Exception as e:
    st.error(f"pandas FAILED: {e}")

try:
    import openpyxl
    st.success(f"openpyxl {openpyxl.__version__} OK")
except Exception as e:
    st.error(f"openpyxl FAILED: {e}")

try:
    import requests
    st.success(f"requests {requests.__version__} OK")
except Exception as e:
    st.error(f"requests FAILED: {e}")

try:
    from sanctions.dilisense_client import DilisenseClient
    st.success("sanctions.dilisense_client OK")
except Exception as e:
    st.error(f"sanctions.dilisense_client FAILED: {e}")

try:
    from sanctions.masterfile import load_masterfile
    st.success("sanctions.masterfile OK")
except Exception as e:
    st.error(f"sanctions.masterfile FAILED: {e}")

try:
    from sanctions.matching import build_query_names
    st.success("sanctions.matching OK")
except Exception as e:
    st.error(f"sanctions.matching FAILED: {e}")
