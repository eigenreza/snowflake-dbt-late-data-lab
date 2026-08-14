"""Entry point of the demo: page navigation and page configuration."""

from __future__ import annotations

import streamlit as st

from common import DEVELOPER, PAGES, PROJECT_LINE, REPO_URL

st.set_page_config(
    page_title="Late Data Lab",
    layout="wide",
    page_icon=":material/schedule:",
    menu_items={
        "Get help": REPO_URL,
        "Report a bug": f"{REPO_URL}/issues",
        "About": f"**Late Data Lab.** {PROJECT_LINE}\n\nDeveloped by {DEVELOPER}. Source code and documentation: {REPO_URL}",
    },
)

sections: dict[str, list] = {}
for number, (script, title, icon, section, _question) in enumerate(PAGES):
    sections.setdefault(section, []).append(st.Page(script, title=title, icon=icon, default=(number == 0)))
st.navigation(sections, expanded=True).run()
