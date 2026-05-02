import streamlit as st
import requests
import pandas as pd

st.set_page_config(page_title="AI Video Factory", layout="wide")

st.title("🎬 AI Video Automation Dashboard")

if st.button("🚀 Start New Video Generation"):
    res = requests.post("http://127.0.0.1:8000/run-pipeline")
    st.success("Pipeline triggered! Refresh in a minute to see progress.")

st.divider()

# Display Task Status
st.subheader("Current Tasks in Pipeline")
tasks = requests.get("http://127.0.0.1:8000/tasks").json()

if tasks:
    df = pd.DataFrame(tasks)
    # Only show relevant columns
    st.table(df[["title", "status", "source"]])
else:
    st.write("No tasks found.")
