"""MSM — the Streamlit operations console.

Run it with ``ecomops ui`` or::

    .venv/bin/streamlit run src/ecomops/app.py

The console drives the same ``EcomopsSystem`` composition root as the CLI and
the HTTP API: same event bus, same approval gate, same agents.

Sign-in is governed by ``ECOMOPS_SSO_MODE``:

* ``dev`` (default) — demo accounts, one per role, password ``msm``;
* ``oidc`` — Streamlit's native OIDC flow (``st.login``);
* ``proxy`` — identity forwarded by oauth2-proxy / Keycloak gatekeeper.

The sidebar is permission-driven: each role only sees the workspaces and
actions it is allowed to use (see :mod:`ecomops.auth`).
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ecomops import __version__, mail, theme
from ecomops.advert import (
    MAX_PHOTOS,
    PORTRAIT,
    SQUARE,
    TONES,
    AdvertCopy,
    AdvertError,
    Notice,
    Offer,
    Photo,
    build_copy,
    build_notice_copy,
    notice_bytes,
    poster_bytes,
)
from ecomops.auth import (
    DEMO_USERS,
    FINANCE_ACTIONS,
    ROLE_LABELS,
    AuthError,
    Permission,
    User,
    autologin_role,
    demo_user,
    dev_login,
    proxy_secret_ok,
    sso_mode,
    user_from_identity,
    user_from_proxy_headers,
)
from ecomops.catalogue import DEFAULT_GROCERY_PATH
from ecomops.chat import OpsChat
from ecomops.chatmodel import (
    DEFAULT_LLAMA_PORT,
    DEFAULT_LOCAL_MODEL,
    ChatModelConfig,
    get_connector,
)
from ecomops.documents import (
    DocumentError,
    ExtractionError,
    OcrUnavailable,
    answer as answer_document,
    discard as discard_document,
    finish as finish_document,
    make_workdir as document_workdir,
    message as document_message,
    open_document,
    parse_answers,
    preview_bytes,
    rows as document_rows,
)
from ecomops.events import ExceptionType
from ecomops.finance import finance_snapshot, finance_summary_line, referral_rows
from ecomops.promotion import CHANNELS, publish_promotion, publish_transit_notice
from ecomops.system import EcomopsSystem, build_system
from ecomops.tasks import TASK_STATUSES, TaskError, task_counts
from ecomops.webimages import describe as describe_image, fetch_item_image

try:  # altair ships with Streamlit, but stay defensive
    import altair as alt
except Exception:  # pragma: no cover - optional
    alt = None

st.set_page_config(
    page_title="MSM",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Editorial styling: obsidian surfaces, champagne accent, serif display type.
st.markdown(
    """
    <style>
      :root {
        --msm-ink:#0b0b0d; --msm-surface:#141417; --msm-panel:#0e0e10; --msm-field:#141417;
        --msm-glow:#1c1811; --msm-ivory:#f2efe8; --msm-muted:#9a968c; --msm-dim:#8f8b81;
        --msm-faint:#6f6b63; --msm-soft:#bdb8ac; --msm-gold:#c8a96a;
        --msm-gold-bright:#d9bd82; --msm-gold-rgb:200,169,106; --msm-on-accent:#0b0b0d;
        --msm-accent-2:#74c2a0; --msm-accent-3:#d98b6a; --msm-accent-4:#8fa8e0;
        --msm-line:rgba(242,239,232,.12); --msm-tint-soft:rgba(255,255,255,.018);
        --msm-tint-mid:rgba(255,255,255,.03); --msm-tint-strong:rgba(255,255,255,.045);
        --msm-serif:"Didot","Bodoni MT","Playfair Display",Georgia,"Times New Roman",serif;
        --msm-sans:"Helvetica Neue",Helvetica,Arial,sans-serif;
      }
      html, body, [class*="css"] {font-family: var(--msm-sans);}
      [data-testid="stAppViewContainer"] {
        background: radial-gradient(1100px 520px at 12% -12%, var(--msm-glow) 0%, var(--msm-ink) 58%)
          fixed, var(--msm-ink);
      }
      [data-testid="stHeader"] {background: transparent;}
      #MainMenu, footer, [data-testid="stDecoration"],
      [data-testid="stStatusWidget"] {display:none !important;}
      /* Keep the header toolbar visible: it holds the sidebar expand control. */
      [data-testid="stToolbar"] {background: transparent; padding: 0 !important;}
      [data-testid="stExpandSidebarButton"],
      [data-testid="stSidebarCollapseButton"] {
        display: inline-flex !important; align-items: center; justify-content: center;
        visibility: visible !important; opacity: 1 !important;
        position: relative; z-index: 1000001;
        width: 2.1rem !important; height: 2.1rem !important; min-height: 2.1rem !important;
        margin: .45rem .1rem 0 .55rem; border-radius: 999px;
        color: var(--msm-gold) !important;
        background: var(--msm-panel) !important;
        border: 1px solid var(--msm-line) !important;
        cursor: pointer;
      }
      [data-testid="stExpandSidebarButton"]:hover,
      [data-testid="stSidebarCollapseButton"]:hover {
        border-color: var(--msm-gold) !important; color: var(--msm-ivory) !important;
      }
      [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"],
      [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"] {
        font-size: 1.15rem !important;
      }
      .block-container, [data-testid="stMainBlockContainer"] {
        padding-top: 1.7rem; padding-bottom: 2.4rem;
        padding-left: clamp(1.4rem, 2.6vw, 3.4rem);
        padding-right: clamp(1.4rem, 2.6vw, 3.4rem);
        max-width: 100% !important;
      }
      h1 {
        font-family: var(--msm-serif) !important; font-weight: 400 !important;
        font-size: clamp(1.95rem, 3.4vw, 2.9rem) !important; letter-spacing: .3em !important;
        line-height: 1 !important; color: var(--msm-ivory) !important; margin: 0 !important;
      }
      h2, h3 {
        font-family: var(--msm-serif) !important; font-weight: 400 !important;
        font-size: 1.22rem !important; letter-spacing: .02em !important;
        color: var(--msm-ivory) !important;
      }
      h3 {padding-bottom: .35rem; border-bottom: 1px solid var(--msm-line); margin: .2rem 0 .6rem !important;}
      p, li, label, .stCaption, [data-testid="stCaptionContainer"] {color: var(--msm-muted);}
      .msm-eyebrow {
        font-size: .53rem; letter-spacing: .42em; text-transform: uppercase;
        color: var(--msm-gold); margin-bottom: .45rem;
      }
      .msm-rule {
        height: 1px; margin: .2rem 0 .65rem;
        background: linear-gradient(90deg,
          var(--msm-gold) 0%, var(--msm-accent-2) 36%,
          var(--msm-accent-3) 68%, var(--msm-accent-4) 100%);
        opacity: .85;
      }
      .msm-tagline {
        font-family: var(--msm-serif); font-size: .9rem; letter-spacing: .04em;
        color: var(--msm-muted); margin-bottom: 1rem;
      }
      [data-testid="stMetric"] {
        background: linear-gradient(155deg, var(--msm-tint-strong), var(--msm-tint-soft));
        border: 1px solid var(--msm-line); border-radius: 2px;
        padding: .5rem .8rem; min-height: 64px;
        height: 100%;
      }
      [data-testid="stHorizontalBlock"] {gap: .6rem;}
      /* Metric rail: flow into as many columns as fit, never stretch orphans. */
      [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {
        display: grid !important;
        grid-template-columns: repeat(auto-fit, minmax(165px, 1fr));
        gap: .6rem;
      }
      [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > div {
        flex: none !important; width: auto !important; min-width: 0 !important;
      }
      [data-testid="stMetricValue"] {
        font-family: var(--msm-serif); font-size: clamp(1.12rem, 1.5vw, 1.4rem);
        font-weight: 400; color: var(--msm-ivory); white-space: nowrap;
        line-height: 1.25;
      }
      [data-testid="stMetricLabel"] {
        font-size: .55rem; text-transform: uppercase; letter-spacing: .12em;
        color: var(--msm-muted); white-space: nowrap; overflow: hidden;
        text-overflow: ellipsis;
      }
      [data-testid="stMetricDelta"] {font-size: .68rem; letter-spacing: .08em;}
      /* Header KPI strip — an editorial data rail: label + number, no cards.
         Hairlines separate the figures; the type does the work. */
      .st-key-msm_stat_rail {
        width: 100% !important;
        margin: .1rem 0 .75rem !important;
        padding: .1rem 0 !important;
        border-top: 1px solid var(--msm-glass-border);
        border-bottom: 1px solid var(--msm-glass-border);
        background: linear-gradient(180deg, var(--msm-glass-soft), transparent);
        border-radius: 2px;
      }
      .st-key-msm_stat_rail [data-testid="stHorizontalBlock"] {
        display: flex !important; flex-wrap: wrap !important;
        gap: 0 !important; align-items: stretch !important;
        width: 100% !important;
      }
      .st-key-msm_stat_rail [data-testid="stHorizontalBlock"] > div {
        flex: 1 1 78px !important; min-width: 0 !important;
        width: auto !important;
      }
      .st-key-msm_stat_rail [data-testid="stMetric"] {
        background: transparent !important;
        border: 0 !important; border-radius: 0 !important;
        box-shadow: none !important; backdrop-filter: none !important;
        -webkit-backdrop-filter: none !important;
        padding: .5rem .7rem .6rem !important; min-height: 0 !important;
        display: flex !important; flex-direction: column !important;
        justify-content: center !important; gap: .18rem !important;
      }
      .st-key-msm_stat_rail [data-testid="stHorizontalBlock"] > div + div
        [data-testid="stMetric"] {
        border-left: 1px solid var(--msm-glass-border) !important;
      }
      .st-key-msm_stat_rail [data-testid="stMetricLabel"],
      .st-key-msm_stat_rail [data-testid="stMetricLabel"] .stMarkdownEllipsis,
      .st-key-msm_stat_rail [data-testid="stMetricLabel"] p {
        font-size: .5rem !important; letter-spacing: .16em !important;
        line-height: 1.2 !important; max-width: 100% !important;
        margin: 0 !important; text-transform: uppercase !important;
        white-space: nowrap !important; overflow: hidden !important;
        text-overflow: ellipsis !important; color: var(--msm-muted) !important;
      }
      .st-key-msm_stat_rail [data-testid="stMetricValue"],
      .st-key-msm_stat_rail [data-testid="stMetricValue"] > div {
        font-family: var(--msm-serif) !important;
        font-size: clamp(1.05rem, 1.35vw, 1.5rem) !important;
        font-weight: 400 !important; line-height: 1.1 !important;
        color: var(--msm-ivory) !important; white-space: nowrap !important;
      }
      .st-key-msm_stat_rail [data-testid="stVerticalBlock"] {gap: 0 !important;}
      .st-key-msm_stat_rail [data-testid="stMetricDelta"] {display: none !important;}
      /* Only reserve sidebar width while it is open: collapsing it must reclaim the space. */
      [data-testid="stSidebar"] {
        background: var(--msm-panel); border-right: 1px solid var(--msm-line);
      }
      [data-testid="stSidebar"][data-collapsed="false"] {min-width: 310px; max-width: 370px;}
      [data-testid="stSidebar"][data-collapsed="true"] {min-width: 0 !important; max-width: 0 !important;}
      [data-testid="stSidebarHeader"] {height: 2.6rem !important; min-height: 2.6rem !important;}
      [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2 {
        letter-spacing: .22em; font-size: 1.15rem !important;
        margin: .1rem 0 .3rem !important;
      }
      [data-testid="stSidebar"] hr {margin: .65rem 0 !important;}
      [data-testid="stSidebar"] .stButton button {padding: .4rem .6rem; font-size: .68rem;}
      .stButton button, [data-testid="stFormSubmitButton"] button,
      [data-testid="stDownloadButton"] button {
        border-radius: 1px; border: 1px solid rgba(var(--msm-gold-rgb),.42);
        background: transparent; color: var(--msm-ivory);
        letter-spacing: .16em; text-transform: uppercase; font-size: .66rem;
        padding: .55rem 1.1rem; transition: all .18s ease;
      }
      .stButton button:hover, [data-testid="stFormSubmitButton"] button:hover,
      [data-testid="stDownloadButton"] button:hover {
        background: var(--msm-gold); color: var(--msm-on-accent); border-color: var(--msm-gold);
      }
      /* Form actions can share a row (analyse + adv generator): tighter tracking keeps
         both labels whole at narrow widths. */
      [data-testid="stFormSubmitButton"] button {
        letter-spacing: .10em; padding-left: .75rem; padding-right: .75rem;
      }
      .stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"],
      [data-testid="stDownloadButton"] button {
        background: var(--msm-gold); color: var(--msm-on-accent); border-color: var(--msm-gold);
      }
      .stButton button p, [data-testid="stFormSubmitButton"] button p,
      [data-testid="stDownloadButton"] button p {color: inherit !important;}
      .stButton button[kind="primary"]:hover {
        background: var(--msm-accent-2); border-color: var(--msm-accent-2);
        color: var(--msm-on-accent);
      }
      [data-testid="stMarkdownContainer"] a, [data-testid="stSidebar"] a {
        color: var(--msm-accent-3) !important;
      }
      /* Action rows: wrap instead of truncating button labels. */
      [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.stButton) {flex-wrap: wrap;}
      [data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.stButton) > div {
        flex: 1 1 190px !important; min-width: 190px !important;
      }
      /* Tabs: this Streamlit build uses ARIA roles, not baseweb data attributes. */
      [data-testid="stTabs"] [role="tablist"] {
        gap: 1.15rem; border-bottom: 1px solid var(--msm-line);
        flex-wrap: wrap; overflow: visible !important;
      }
      [data-testid="stTabs"] [role="tab"] {
        position: relative; background: transparent; padding: .45rem 0;
        color: var(--msm-muted);
      }
      [data-testid="stTabs"] [role="tab"] p {
        font-size: .6rem !important; letter-spacing: .12em !important;
        text-transform: uppercase; margin: 0 !important; color: inherit;
      }
      [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
        color: var(--msm-gold) !important; box-shadow: none;
      }
      [data-testid="stTabs"] [role="tab"][aria-selected="true"]::after {
        content: ""; position: absolute; left: 0; right: 0; bottom: 0;
        height: 2px; background: linear-gradient(90deg,
          var(--msm-gold), var(--msm-accent-2),
          var(--msm-accent-3), var(--msm-accent-4));
      }
      [data-testid="stTabs"] [role="tab"][aria-selected="true"] p {color: var(--msm-gold) !important;}
      .stTabs [data-baseweb="tab-list"] {gap: 1.5rem; border-bottom: 1px solid var(--msm-line);}
      .stTabs [data-baseweb="tab"] {
        background: transparent; padding: .7rem 0; font-size: .66rem;
        letter-spacing: .18em; text-transform: uppercase; color: var(--msm-muted);
      }
      .stTabs [aria-selected="true"] {color: var(--msm-gold) !important;}
      .stTabs [data-baseweb="tab-highlight"] {background-color: var(--msm-gold); height: 2px;}
      .stTabs [data-baseweb="tab-border"] {display:none;}
      /* Controls that Streamlit paints with the static config accent follow the
         live palette instead, so no champagne leaks into a mint or rose skin. */
      [data-testid="stCheckbox"] label[data-selected="true"] > div:first-of-type {
        background: var(--msm-gold) !important;
        border-color: var(--msm-gold) !important;
      }
      [data-testid="stRadio"] label:has(input:checked) > div > div:first-child {
        background: var(--msm-gold) !important;
        border-color: var(--msm-gold) !important;
      }
      [data-testid="stSlider"] [role="slider"] {
        border-color: var(--msm-gold) !important;
      }
      [data-testid="stProgress"] div[role="progressbar"] > div {
        background: var(--msm-gold) !important;
      }
      div[data-testid="stForm"] {
        border: 1px solid var(--msm-line); border-radius: 2px;
        padding: 1.1rem 1.25rem; background: var(--msm-tint-soft);
      }
      [data-testid="stDataFrame"] {border: 1px solid var(--msm-line); border-radius: 2px;}
      [data-testid="stChatInput"] > div {
        background: var(--msm-tint-mid) !important;
        border: 1px solid var(--msm-line) !important; border-radius: 2px !important;
      }
      [data-testid="stChatInput"] textarea {
        color: var(--msm-ivory) !important;
        /* The sidebar composer must stay inside the viewport; long text scrolls. */
        height: 2.6rem !important; min-height: 2.6rem !important;
        max-height: 8rem !important;
      }
      [data-testid="stChatInputSubmitButton"] {
        background: rgba(var(--msm-gold-rgb),.16) !important;
        border: 1px solid rgba(var(--msm-gold-rgb),.42) !important;
        color: var(--msm-gold) !important;
      }
      [data-testid="stChatInputSubmitButton"]:hover {
        background: var(--msm-gold) !important; color: var(--msm-on-accent) !important;
      }
      [data-testid="stChatInputSubmitButton"] svg {fill: currentColor;}
      [data-testid="stExpander"] {border: 1px solid var(--msm-line); border-radius: 2px; background: transparent;}
      [data-testid="stAlert"] {border-radius: 2px; border: 1px solid var(--msm-line);}
      [data-testid="stAlertContainer"] {
        background: rgba(var(--msm-gold-rgb),.08) !important;
        border: 1px solid rgba(var(--msm-gold-rgb),.32) !important;
        border-radius: 2px !important; color: var(--msm-ivory) !important;
      }
      [data-testid="stAlertContainer"] p, [data-testid="stAlertContainer"] span {
        color: var(--msm-ivory) !important;
      }
      [data-testid="stChatMessage"] {
        background: var(--msm-tint-soft); border: 1px solid var(--msm-line); border-radius: 2px;
      }
      [data-testid="stChatMessageAvatarUser"],
      [data-testid="stChatMessageAvatarAssistant"] {
        background: rgba(var(--msm-gold-rgb),.12) !important;
        border: 1px solid rgba(var(--msm-gold-rgb),.38) !important;
        border-radius: 2px !important;
      }
      [data-testid="stChatMessageAvatarUser"] [data-testid="stIconMaterial"],
      [data-testid="stChatMessageAvatarAssistant"] [data-testid="stIconMaterial"] {
        color: var(--msm-gold) !important; font-size: 1rem !important;
      }
      .msm-chip {
        display:inline-block; padding:.14rem .55rem; border-radius:999px;
        border:1px solid var(--msm-accent-2); background:rgba(var(--msm-gold-rgb),.10);
        color:var(--msm-gold); font-size:.6rem; letter-spacing:.18em;
        text-transform:uppercase; font-weight:600;
      }
      .msm-sub {color:var(--msm-dim); font-size:.8rem; letter-spacing:.04em; margin-top:-.1rem;}
      .msm-chat-head {
        font-family: var(--msm-serif); font-size: .92rem; letter-spacing: .16em;
        text-transform: uppercase; color: var(--msm-ivory);
      }
      .msm-chat-model {
        font-size: .55rem; letter-spacing: .1em; text-transform: uppercase;
        color: var(--msm-faint); margin: .2rem 0 .7rem; padding-bottom: .5rem;
        border-bottom: 1px solid var(--msm-line);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      .msm-chat-empty {
        font-family: var(--msm-serif); font-size: .95rem; line-height: 1.5;
        color: var(--msm-soft); margin: .1rem 0 .7rem;
      }
      .msm-chat-hint {
        font-size: .62rem; letter-spacing: .08em; line-height: 1.6;
        text-transform: uppercase; color: var(--msm-faint);
      }
      .msm-doc-row {
        display:flex; flex-direction:column; gap:.05rem;
        padding:.34rem 0; border-bottom:1px solid rgba(var(--msm-gold-rgb),.12);
      }
      .msm-doc-field {
        font-size:.56rem; letter-spacing:.16em; text-transform:uppercase; color:var(--msm-dim);
      }
      .msm-doc-value {
        font-family: var(--msm-serif); font-size:.92rem; color: var(--msm-ivory);
        line-height:1.25;
      }
      /* Chat panel: fit the viewport so the input never falls below the fold. */
      [data-testid="stSidebar"] [data-testid="stLayoutWrapper"]:has(.st-key-chat_panel) {
        height: clamp(150px, calc(100vh - 455px), 320px) !important;
        min-height: 0 !important; flex: 0 0 auto !important;
      }
      .st-key-chat_panel {
        height: 100% !important; min-height: 0 !important;
        flex: 1 1 auto !important;
      }
      /* Theme bar: a fixed pill in the top-right corner, above the header. */
      .st-key-msm_theme_bar {
        position: fixed; top: .55rem; right: 1.05rem;
        width: auto !important; z-index: 1000002;
      }
      .st-key-msm_theme_bar [data-testid="stPopover"] button {
        padding: .3rem .7rem; min-height: 2rem; font-size: .58rem;
        letter-spacing: .14em; background: var(--msm-panel);
        border-color: var(--msm-line);
        background-image: linear-gradient(120deg,
          color-mix(in srgb, var(--msm-gold) 16%, transparent),
          color-mix(in srgb, var(--msm-accent-2) 14%, transparent) 40%,
          color-mix(in srgb, var(--msm-accent-3) 14%, transparent) 70%,
          color-mix(in srgb, var(--msm-accent-4) 16%, transparent));
      }
      .st-key-msm_theme_bar [data-testid="stPopover"] button:hover {
        color: var(--msm-on-accent);
      }
      .msm-swatch {
        display: inline-block; width: .7rem; height: .7rem; border-radius: 999px;
        border: 1px solid; vertical-align: middle; margin-right: .18rem;
      }
      .msm-mark {
        background: linear-gradient(90deg,
          var(--msm-gold), var(--msm-accent-2),
          var(--msm-accent-3), var(--msm-accent-4));
        -webkit-background-clip: text; background-clip: text;
        color: transparent !important;
      }
      .msm-theme-head {
        font-size: .55rem; letter-spacing: .22em; text-transform: uppercase;
        color: var(--msm-gold); margin-bottom: .5rem;
      }
      .msm-theme-preview {
        display: flex; align-items: center; flex-wrap: wrap; gap: .3rem;
        margin-top: .7rem; font-size: .66rem; line-height: 1.5; color: var(--msm-dim);
      }
      .msm-theme-preview .msm-swatch {margin-right: 0;}
      .msm-style-badge {
        padding: .1rem .35rem; border-radius: 999px;
        border: 1px solid var(--msm-glass-border);
        background: var(--msm-glass-hi);
        color: var(--msm-gold); font-size: .55rem;
        letter-spacing: .12em; text-transform: uppercase;
      }
      /* Task cards follow the active skin's glass/flat material. */
      .msm-task-card {
        padding: .65rem .8rem; margin: 0 0 .55rem;
        background: var(--msm-glass-soft);
        border: 1px solid var(--msm-glass-border);
        border-radius: 14px; backdrop-filter: var(--msm-blur-mid);
        -webkit-backdrop-filter: var(--msm-blur-mid);
        box-shadow: inset 0 1px 0 var(--msm-glass-hi);
      }
      .msm-task-title {
        font-family: var(--msm-serif); font-size: 1.02rem;
        color: var(--msm-ivory); margin: 0 0 .3rem;
      }
      .msm-task-badge {
        display: inline-block; margin-right: .35rem;
        padding: .08rem .4rem; border-radius: 999px;
        border: 1px solid var(--msm-glass-border);
        font-size: .54rem; letter-spacing: .12em; text-transform: uppercase;
        color: var(--msm-gold); background: var(--msm-glass-hi);
      }
      .msm-task-badge[data-p="high"] {color: var(--msm-accent-3);}
      .msm-task-badge[data-p="low"] {color: var(--msm-accent-2);}
      .msm-task-meta {
        font-size: .63rem; letter-spacing: .06em;
        color: var(--msm-muted); line-height: 1.7;
      }
      .msm-task-meta b {color: var(--msm-soft); font-weight: 500;}
      [data-testid="stPopoverBody"] {
        width: 330px !important; max-width: min(90vw, 330px) !important;
      }
      [data-testid="stPopoverBody"] [data-testid="stButtonGroup"] {
        display: block !important;
      }
      [data-testid="stPopoverBody"] [data-testid="stButtonGroup"] > div {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: .3rem; width: 100% !important;
      }
      [data-testid="stPopoverBody"] [data-testid="stButtonGroup"] button,
      [data-testid="stPopoverBody"] [data-testid="stButtonGroup"] button * {
        transition: none !important;
      }
      [data-testid="stPopoverBody"] [data-testid="stButtonGroup"] button {
        width: 100%; justify-content: center; min-height: 2rem;
        padding: .35rem .4rem; font-size: .66rem; letter-spacing: .03em;
        background: var(--msm-field); border: 1px solid var(--msm-line);
        color: var(--msm-ivory);
      }
      [data-testid="stPopoverBody"] [data-testid="stButtonGroup"] button:hover {
        border-color: var(--msm-gold); color: var(--msm-gold-bright);
      }
      [data-testid="stPopoverBody"] [data-testid="stButtonGroup"] button[aria-checked="true"] {
        background: var(--msm-gold) !important; border-color: var(--msm-gold) !important;
        color: var(--msm-on-accent) !important; font-weight: 600;
      }
      .msm-theme-sub {margin-top: .9rem;}
      /* Glass material: frosted panels over the four-colour aurora. */
      [data-testid="stMainBlockContainer"] {
        background: var(--msm-glass-soft);
        backdrop-filter: var(--msm-blur);
        -webkit-backdrop-filter: var(--msm-blur);
        border: 1px solid var(--msm-glass-border);
        border-radius: 22px;
        box-shadow: var(--msm-shadow);
        margin: .55rem .8rem 1rem .45rem;
        min-height: calc(100vh - 4.2rem);
      }
      [data-testid="stSidebar"] {
        background: var(--msm-glass) !important;
        backdrop-filter: var(--msm-blur-strong);
        -webkit-backdrop-filter: var(--msm-blur-strong);
        border-right: 1px solid var(--msm-glass-border);
        box-shadow: 12px 0 44px rgba(0,0,0,.22);
      }
      [data-testid="stSidebarContent"], [data-testid="stSidebar"] > div {
        background: transparent !important;
      }
      [data-testid="stMetric"] {
        background: linear-gradient(155deg, var(--msm-glass-strong), var(--msm-glass));
        backdrop-filter: var(--msm-blur-mid);
        -webkit-backdrop-filter: var(--msm-blur-mid);
        border: 1px solid var(--msm-glass-border);
        border-radius: 16px;
        box-shadow: 0 12px 34px rgba(0,0,0,.22), inset 0 1px 0 var(--msm-glass-hi);
      }
      .stButton button, [data-testid="stFormSubmitButton"] button,
      [data-testid="stDownloadButton"] button {
        border-radius: 999px; border: 1px solid var(--msm-glass-border);
        background: var(--msm-glass-soft); backdrop-filter: var(--msm-blur-soft);
        box-shadow: inset 0 1px 0 var(--msm-glass-hi);
      }
      .stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"],
      [data-testid="stDownloadButton"] button {
        border-radius: 999px;
      }
      div[data-testid="stForm"], [data-testid="stExpander"],
      [data-testid="stAlertContainer"], [data-testid="stDataFrame"],
      [data-testid="stChatInput"] > div, [data-testid="stChatMessage"] {
        background: var(--msm-glass-soft) !important;
        border: 1px solid var(--msm-glass-border) !important;
        border-radius: 16px !important;
        backdrop-filter: var(--msm-blur-mid);
        -webkit-backdrop-filter: var(--msm-blur-mid);
        box-shadow: inset 0 1px 0 var(--msm-glass-hi);
      }
      [data-testid="stTabs"] [role="tablist"] {
        background: var(--msm-glass-soft);
        border: 1px solid var(--msm-glass-border);
        border-radius: 999px; padding: .28rem .55rem;
      }
      [data-testid="stTabs"] [role="tab"] {border-radius: 999px; padding: .4rem .7rem;}
      [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
        background: var(--msm-glass-strong);
      }
      [data-testid="stPopoverBody"] {
        background: var(--msm-glass-strong) !important;
        backdrop-filter: var(--msm-blur-strong);
        -webkit-backdrop-filter: var(--msm-blur-strong);
        border: 1px solid var(--msm-glass-border) !important;
        border-radius: 18px !important;
        box-shadow: var(--msm-shadow);
      }
      .st-key-msm_theme_bar [data-testid="stPopover"] button {
        backdrop-filter: var(--msm-blur-mid); border-radius: 999px;
      }
      [data-baseweb="select"] > div, [data-baseweb="input"] > div,
      [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
      [data-testid="stNumberInput"] input, [data-testid="stFileUploaderDropzone"] {
        border-radius: 12px !important; backdrop-filter: var(--msm-blur-hair);
      }

      /* ===================== OPS DASHBOARD v2 ===================== */
      /* Full-width canvas — content sections carry the material, not the page. */
      [data-testid="stMainBlockContainer"] {
        background: transparent !important; border: 0 !important;
        border-radius: 0 !important; box-shadow: none !important;
        backdrop-filter: none !important; margin: 0 !important;
        padding: 1rem clamp(1rem, 2.1vw, 2.4rem) 3rem !important;
      }
      .block-container {max-width: 100% !important; padding: 0 !important;}
      .st-key-msm_header {padding: .15rem 0 .75rem !important;}
      .st-key-msm_header h1 {
        font-size: 1.02rem !important; letter-spacing: .38em !important;
        line-height: 1.2 !important;
      }
      .st-key-msm_header .msm-eyebrow {
        font-size: .47rem !important; letter-spacing: .32em !important;
        margin-bottom: .2rem !important;
      }
      .msm-page-head {
        display: flex; align-items: baseline; justify-content: space-between;
        gap: 1rem; margin-top: .1rem;
      }
      .msm-page-label {
        font-family: var(--msm-serif); font-size: 1.6rem;
        color: var(--msm-ivory); letter-spacing: .01em;
      }
      .msm-page-meta {
        font-size: .55rem; letter-spacing: .14em; text-transform: uppercase;
        color: var(--msm-faint); white-space: nowrap;
      }
      h3 {
        font-family: var(--msm-sans) !important; font-size: .68rem !important;
        letter-spacing: .2em !important; text-transform: uppercase !important;
        color: var(--msm-muted) !important; border: 0 !important;
        margin: 1.35rem 0 .75rem !important; padding: 0 0 .45rem .7rem !important;
        position: relative;
      }
      h3::before {
        content: ""; position: absolute; left: 0; top: .12rem; bottom: .25rem;
        width: 2px; border-radius: 2px; background: var(--msm-gold);
      }
      h2 {font-size: 1.35rem !important;}
      div[data-testid="stForm"], [data-testid="stExpander"],
      [data-testid="stDataFrame"], [data-testid="stAlertContainer"] {
        border-radius: 14px !important; padding: 1rem 1.15rem !important;
        margin: .35rem 0 1rem !important;
      }
      [data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button,
      [data-testid="stDownloadButton"] button {
        font-weight: 600 !important; letter-spacing: .1em !important;
        transition: background .16s ease, border-color .16s ease, transform .16s ease !important;
      }
      [data-testid="stButton"] button:hover,
      [data-testid="stFormSubmitButton"] button:hover {transform: translateY(-1px);}
      /* Sidebar now holds tools only; navigation sits beside the theme pill. */
      [data-testid="stSidebar"][data-collapsed="false"] {
        min-width: 238px !important; max-width: 238px !important;
      }
      [data-testid="stSidebar"] {
        background: var(--msm-panel) !important;
        border-right: 1px solid var(--msm-glass-border) !important;
      }
      [data-testid="stSidebar"] .block-container {padding: 1rem .8rem !important;}
      .msm-brand {
        font-family: var(--msm-serif); font-size: 1.05rem; letter-spacing: .32em;
        color: var(--msm-ivory); margin: .2rem 0 .55rem;
      }
      .msm-brand .msm-mark {letter-spacing: 0; margin-right: .35rem;}
      .msm-chip {display: table !important; margin-bottom: .2rem !important;}
      .msm-sub {display: block !important; margin: 0 !important;}
      /* Icon strip: one outlined icon per workspace, hover reveals the name. */
      .st-key-msm_icon_bar {
        position: fixed; top: .55rem; right: 8.4rem; left: auto !important;
        width: max-content !important; flex: 0 0 auto !important;
        z-index: 1000004;
      }
      .st-key-msm_icon_bar [data-testid="stLayoutWrapper"] {
        width: max-content !important; flex: 0 0 auto !important;
      }
      .st-key-msm_icon_bar [data-testid="stHorizontalBlock"] {
        gap: .28rem !important; flex-wrap: nowrap !important;
        width: max-content !important; flex: 0 0 auto !important;
      }
      [data-testid="stMain"] .st-key-msm_icon_bar
        [data-testid="stHorizontalBlock"] > div {
        width: 2.15rem !important; min-width: 2.15rem !important;
        flex: 0 0 2.15rem !important;
      }
      .st-key-msm_icon_bar button {
        width: 2.15rem !important; height: 2rem !important;
        min-height: 2rem !important; padding: 0 !important;
        border-radius: 8px !important; border: 1px solid var(--msm-gold) !important;
        background: linear-gradient(155deg,
          color-mix(in srgb, var(--msm-ivory) 8%, transparent),
          var(--msm-glass-soft)) !important;
        backdrop-filter: var(--msm-blur-soft) !important;
        color: var(--msm-gold) !important;
        display: flex !important; align-items: center !important;
        justify-content: center !important;
        /* Bevelled 3D key: lit top edge, shaded base, soft contact shadow. */
        box-shadow:
          inset 0 1px 0 rgb(255 255 255 / .24),
          inset 1px 0 0 rgb(255 255 255 / .10),
          inset 0 -2px 3px rgb(0 0 0 / .30),
          0 3px 0 color-mix(in srgb, currentColor 52%, #000),
          0 6px 12px rgb(0 0 0 / .32) !important;
        transform: translateY(0) !important;
        transition: transform .12s ease, box-shadow .12s ease !important;
      }
      .st-key-msm_icon_bar button [data-testid="stIconMaterial"] {
        color: inherit !important; font-size: 1.05rem !important;
        margin: 0 !important;
        filter: drop-shadow(0 1px 0 rgb(0 0 0 / .45));
      }
      .st-key-msm_icon_bar button:hover {
        color: var(--msm-ivory) !important;
        transform: translateY(-1px) !important;
        box-shadow:
          inset 0 1px 0 rgb(255 255 255 / .32),
          inset 0 -2px 3px rgb(0 0 0 / .26),
          0 4px 0 color-mix(in srgb, currentColor 52%, #000),
          0 9px 16px rgb(0 0 0 / .38) !important;
      }
      .st-key-msm_icon_bar button:active,
      .st-key-msm_icon_bar button[kind="primary"] {
        transform: translateY(2px) !important;
        box-shadow:
          inset 0 2px 5px rgb(0 0 0 / .45),
          0 1px 0 color-mix(in srgb, currentColor 52%, #000) !important;
      }
      /* Four hues cycle along the strip, as everywhere else in the console. */
      .st-key-msm_icon_bar [data-testid="stHorizontalBlock"] > div:nth-child(4n+2) button {
        border-color: var(--msm-accent-2) !important; color: var(--msm-accent-2) !important;
      }
      .st-key-msm_icon_bar [data-testid="stHorizontalBlock"] > div:nth-child(4n+3) button {
        border-color: var(--msm-accent-3) !important; color: var(--msm-accent-3) !important;
      }
      .st-key-msm_icon_bar [data-testid="stHorizontalBlock"] > div:nth-child(4n+4) button {
        border-color: var(--msm-accent-4) !important; color: var(--msm-accent-4) !important;
      }
      .st-key-msm_icon_bar button[kind="primary"] {
        background: var(--msm-gold) !important; color: var(--msm-on-accent) !important;
      }
      .st-key-msm_icon_bar [data-testid="stHorizontalBlock"] > div:nth-child(4n+2)
        button[kind="primary"] {background: var(--msm-accent-2) !important; color: var(--msm-ink) !important;}
      .st-key-msm_icon_bar [data-testid="stHorizontalBlock"] > div:nth-child(4n+3)
        button[kind="primary"] {background: var(--msm-accent-3) !important; color: var(--msm-ink) !important;}
      .st-key-msm_icon_bar [data-testid="stHorizontalBlock"] > div:nth-child(4n+4)
        button[kind="primary"] {background: var(--msm-accent-4) !important; color: var(--msm-ink) !important;}
      /* KPI strip: six to a row, two lines of label allowed, values never clipped. */
      .st-key-msm_stat_rail [data-testid="stHorizontalBlock"] {
        display: grid !important; grid-template-columns: repeat(6, minmax(0, 1fr)) !important;
      }
      .st-key-msm_stat_rail [data-testid="stMetricLabel"],
      .st-key-msm_stat_rail [data-testid="stMetricLabel"] p {
        white-space: normal !important;
        display: -webkit-box !important; -webkit-box-orient: vertical;
        -webkit-line-clamp: 2; overflow: hidden !important;
        line-height: 1.25 !important;
      }
      .st-key-msm_stat_rail [data-testid="stMetric"] {
        padding: .5rem .62rem .6rem !important;
      }
      .st-key-msm_stat_rail [data-testid="stHorizontalBlock"] > div:nth-child(6n+1)
        [data-testid="stMetric"] {border-left: 0 !important;}
      @media (max-width: 1180px) {
        .st-key-msm_stat_rail [data-testid="stHorizontalBlock"] {
          grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
        }
        .st-key-msm_stat_rail [data-testid="stHorizontalBlock"] > div:nth-child(6n+1)
          [data-testid="stMetric"] {border-left: 1px solid var(--msm-glass-border) !important;}
        .st-key-msm_stat_rail [data-testid="stHorizontalBlock"] > div:nth-child(4n+1)
          [data-testid="stMetric"] {border-left: 0 !important;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# Seed widget state before the widgets exist so the first get_system() call
# already sees the final configuration (no spurious rebuild on first click).
_ENV_GROCERY = os.environ.get("ECOMOPS_GROCERY", "").strip()
_GROCERY_OFF = _ENV_GROCERY.lower() in {"0", "off", "none", "false"}
st.session_state.setdefault("backend", "Rule-based (deterministic)")
st.session_state.setdefault("model", "mlx-community/Qwen3-4B-4bit")
st.session_state.setdefault(
    "grocery_enabled",
    (not _GROCERY_OFF) and (bool(_ENV_GROCERY) or DEFAULT_GROCERY_PATH.is_file()),
)
st.session_state.setdefault(
    "grocery_path", "" if _GROCERY_OFF else (_ENV_GROCERY or str(DEFAULT_GROCERY_PATH))
)
st.session_state.setdefault("grocery_limit", int(os.environ.get("ECOMOPS_GROCERY_LIMIT") or 0))
st.session_state.setdefault("chat_history", [])
st.session_state.setdefault("theme_choice", theme.DEFAULT_THEME)
st.session_state.setdefault("theme_last", theme.DEFAULT_THEME)
st.session_state.setdefault("theme_contrast", theme.DEFAULT_CONTRAST)

# Inbox defaults come from ECOMOPS_MAIL_*; edits stay in this browser session.
_default_mail = mail.config_from_env()
st.session_state.setdefault("mail_host", _default_mail.host)
st.session_state.setdefault("mail_port", _default_mail.port)
st.session_state.setdefault("mail_user", _default_mail.username)
st.session_state.setdefault("mail_password", _default_mail.password)
st.session_state.setdefault("mail_folder", _default_mail.folder)
st.session_state.setdefault("mail_limit", _default_mail.limit)
st.session_state.setdefault("mail_ssl", _default_mail.use_ssl)
st.session_state.setdefault("mail_mark_seen", _default_mail.mark_seen)

_default_chat = ChatModelConfig.from_environment()
st.session_state.setdefault("chat_mode", _default_chat.mode)
st.session_state.setdefault("chat_base_url", _default_chat.base_url)
st.session_state.setdefault("chat_model", _default_chat.model)
st.session_state.setdefault("chat_api_key", _default_chat.api_key)
st.session_state.setdefault("chat_gguf", _default_chat.gguf_path)
st.session_state.setdefault("chat_port", _default_chat.port)
st.session_state.setdefault("chat_ctx", _default_chat.context)


def _chat_config() -> ChatModelConfig:
    """Build the connector config from the sidebar widgets and install it."""
    raw_mode = str(st.session_state.get("chat_mode") or "off")
    mode = raw_mode if raw_mode in {"off", "endpoint", "gguf"} else "off"
    config = ChatModelConfig(
        mode=mode,
        base_url=str(st.session_state.get("chat_base_url") or "").strip()
        or _default_chat.base_url,
        model=str(st.session_state.get("chat_model") or "").strip(),
        api_key=str(st.session_state.get("chat_api_key") or "lemonade"),
        gguf_path=str(st.session_state.get("chat_gguf") or "").strip(),
        port=int(st.session_state.get("chat_port") or DEFAULT_LLAMA_PORT),
        context=int(st.session_state.get("chat_ctx") or 8192),
    )
    get_connector(config)
    return config


def _mail_config() -> mail.MailConfig:
    """Current inbox settings, sensitive fields never rendered as text."""
    return mail.MailConfig(
        host=str(st.session_state.get("mail_host") or "").strip(),
        port=int(st.session_state.get("mail_port") or 993),
        username=str(st.session_state.get("mail_user") or "").strip(),
        password=str(st.session_state.get("mail_password") or ""),
        folder=str(st.session_state.get("mail_folder") or "INBOX").strip() or "INBOX",
        limit=int(st.session_state.get("mail_limit") or 20),
        use_ssl=bool(st.session_state.get("mail_ssl", True)),
        mark_seen=bool(st.session_state.get("mail_mark_seen", False)),
    )


def _palette() -> theme.Theme:
    """The live console palette (charts need concrete colours, not CSS vars)."""
    return theme.get(st.session_state.get("theme_choice"))


def _lock_theme() -> None:
    """Keep a palette even if a pill is deselected by a second click.

    This runs at the top of every pass, never as a widget callback: writing to
    the widget's own key from ``on_change`` makes Streamlit apply the value one
    rerun late, so the CSS trails the selected pill by a click.
    """
    chosen = (
        st.session_state.get("theme_choice")
        or st.session_state.get("theme_last")
        or theme.DEFAULT_THEME
    )
    st.session_state["theme_choice"] = chosen
    st.session_state["theme_last"] = chosen


def _lock_contrast() -> None:
    st.session_state["theme_contrast"] = theme.get_contrast(
        st.session_state.get("theme_contrast")
    )


_lock_theme()
_lock_contrast()

# The palette layer sits after the base stylesheet so its variables win the
# cascade. Switching a pill only changes these variables: no domain rebuild.
st.markdown(theme.css(st.session_state["theme_choice"]), unsafe_allow_html=True)
_contrast_layer = theme.contrast_css(
    st.session_state["theme_choice"], st.session_state["theme_contrast"]
)
if _contrast_layer:
    st.markdown(_contrast_layer, unsafe_allow_html=True)


def _render_theme_bar() -> None:
    """Top-right palette switch, available on the login screen too."""
    # Nesting matters here: the popover must live inside the keyed container
    # that CSS pins to the top-right corner.
    with st.container(key="msm_theme_bar"):  # noqa: SIM117
        with st.popover(
            "Theme",
            icon=":material/palette:",
            help="Switch the console palette",
            width="content",
        ):
            st.markdown("<div class='msm-theme-head'>Console theme</div>", unsafe_allow_html=True)
            st.pills(
                "Console theme",
                options=theme.names(),
                format_func=lambda key: theme.get(key).label,
                key="theme_choice",
                label_visibility="collapsed",
            )
            active = _palette()
            st.markdown(
                f"<div class='msm-theme-preview'>{theme.swatch(active.key)}"
                f"<span class='msm-style-badge'>{active.style_label}</span>"
                f"<span>{active.blurb}</span></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<div class='msm-theme-head msm-theme-sub'>Contrast</div>",
                unsafe_allow_html=True,
            )
            st.pills(
                "Contrast",
                options=list(theme.contrasts()),
                format_func=lambda key: theme.contrasts()[key],
                key="theme_contrast",
                label_visibility="collapsed",
            )


_render_theme_bar()

DEMO_TEXT = (
    "Hi, I need 2 pairs of wireless bluetooth earbuds delivered to Berlin ASAP, "
    "and do you have an aluminium laptop stand in stock? Also the noise cancelling headphones."
)

RISK_COLORS = {"low": "#8fbf8f", "medium": "#d9b26a", "high": "#d98b7a"}


# --------------------------------------------------------------------------- #
# State helpers
# --------------------------------------------------------------------------- #

def _grocery_selection() -> tuple[str | None, int | None]:
    """(path, limit) for the merged grocery pack, or (None, None) when off/invalid."""
    if not st.session_state.get("grocery_enabled"):
        return None, None
    raw = str(st.session_state.get("grocery_path") or "").strip()
    if not raw:
        return None, None
    path = Path(raw).expanduser()
    if not path.is_file():
        return None, None
    limit = int(st.session_state.get("grocery_limit") or 0)
    return str(path.resolve()), (limit or None)


def get_system() -> EcomopsSystem:
    """Return the session's system, rebuilding it if the configuration changed."""
    grocery, grocery_limit = _grocery_selection()
    cfg = {
        "use_mlx": st.session_state.get("backend") == "MLX (local model)",
        "model": st.session_state.get("model") or None,
        "grocery": grocery,
        "grocery_limit": grocery_limit,
    }
    if "system" not in st.session_state or st.session_state.get("cfg") != cfg:
        first_build = "system" not in st.session_state
        st.session_state.system = build_system(**cfg)
        st.session_state.cfg = cfg
        if not first_build:
            st.session_state.flash = "Configuration changed — event history cleared."
    return st.session_state.system


def run(coro: Any) -> Any:
    """Run one system entry point (they are async) in a fresh event loop."""
    return asyncio.run(coro)


def can_approve(user: User, event: Any) -> bool:
    """Money movements need ``approve_finance``; everything else ``approve_operations``."""
    required = (
        Permission.APPROVE_FINANCE
        if getattr(event, "action", "") in FINANCE_ACTIONS
        else Permission.APPROVE_OPERATIONS
    )
    return user.can(required)


def settle(system: EcomopsSystem, *, granted: bool, user: User) -> int:
    """Resolve every pending proposal this user may decide; returns decisions made."""
    decided = 0
    for _ in range(100):                      # loop guard: a decision may unblock more work
        pending = [e for e in system.pending_approvals if can_approve(user, e)]
        if not pending:
            break
        for event in pending:
            run(system.decide(event.proposal_id, granted=granted, decided_by=user.email))
            decided += 1
    return decided


def flash(msg: str) -> None:
    st.session_state.flash = msg


def seed_sample_events(system: EcomopsSystem, user: User) -> None:
    """Deterministic sample history for the Event trace tab (no model required)."""
    run(system.receive_customer_request("CUST-001", DEMO_TEXT))
    run(system.report_stock_levels())
    settle(system, granted=True, user=user)
    routed = system.bus.of_type("ShipmentRouted")
    if routed:
        run(system.report_delivery_exception(routed[0].shipment_id, "carrier_delay", "hub strike in transit"))
        settle(system, granted=True, user=user)
    if user.can(Permission.APPROVE_FINANCE) and routed:
        ctx = next(iter(system.shipments().values()))
        product = system.inventory.products[ctx["sku"]]
        run(system.receive_customer_request(
            ctx["customer_id"], f"I want a refund for the {product.name}, it arrived damaged"
        ))
        settle(system, granted=True, user=user)


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

def _groups(value: Any) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return tuple(str(item) for item in parsed)
        except json.JSONDecodeError:
            pass
        return tuple(part.strip() for part in value.split(",") if part.strip())
    return ()


def _resolve_user() -> User | None:
    role = autologin_role()
    if role is not None:
        return demo_user(role)

    mode = sso_mode()
    if mode == "dev":
        user = st.session_state.get("user")
        return user if isinstance(user, User) else None

    if mode == "oidc":
        try:
            if not st.user.is_logged_in:
                return None
            email = str(st.user.get("email") or st.user.get("preferred_username") or "")
            name = str(st.user.get("name") or "") or None
            groups = _groups(st.user.get("groups"))
        except Exception:
            return None
        try:
            return user_from_identity(email, name, groups=groups, provider="oidc")
        except AuthError:
            return None

    # proxy mode: identity injected by oauth2-proxy / gatekeeper
    try:
        headers = {str(k).lower(): v for k, v in dict(st.context.headers).items()}
    except Exception:
        return None
    if not proxy_secret_ok(headers):
        return None
    try:
        return user_from_proxy_headers(headers)
    except AuthError:
        return None


def _render_login() -> None:
    mode = sso_mode()
    left, right = st.columns([3, 2])
    with left:
        st.markdown("<div class='msm-eyebrow'>The Operations Atelier</div>", unsafe_allow_html=True)
        st.title("MSM")
        st.markdown("<div class='msm-rule'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='msm-tagline'>Event-driven commerce, executed with precision.</div>",
            unsafe_allow_html=True,
        )
        if mode == "oidc":
            st.write("Sign in with your organisation account to open the console.")
            if st.button("Sign in with SSO", type="primary"):
                try:
                    st.login()
                except Exception as exc:  # configuration problem, not a credential problem
                    st.error(f"SSO is not configured yet: {exc}")
        elif mode == "proxy":
            st.error(
                "No identity was forwarded by the reverse proxy. Open MSM through the "
                "SSO gateway (oauth2-proxy) and try again."
            )
        else:
            st.write("Demo sign-in — one account per role.")
            with st.form("msm-login"):
                email = st.selectbox(
                    "Account",
                    list(DEMO_USERS),
                    format_func=lambda e: f"{DEMO_USERS[e][0]} · {ROLE_LABELS[DEMO_USERS[e][1]]}",
                )
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign in", type="primary")
            if submitted:
                try:
                    st.session_state.user = dev_login(email, password)
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            st.caption(
                f"Demo password: `{os.environ.get('ECOMOPS_DEMO_PASSWORD', 'msm')}` · "
                "set `ECOMOPS_SSO_MODE=oidc` for real SSO."
            )
    with right:
        st.markdown("#### Roles")
        st.caption("MD — full access")
        st.caption("Supervisor — everything except finance")
        st.caption("Accountant — finance and refunds only")
        st.caption("Staff — day-to-day actions only")
        st.caption(f"Mode: `{mode}` · v{__version__}")


user = _resolve_user()
if user is None:
    _render_login()
    st.stop()

system = get_system()


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

def _sign_out() -> None:
    discard_document(st.session_state.get("doc_session"))
    st.session_state.pop("doc_session", None)
    st.session_state.pop("doc_output", None)
    st.session_state.pop("user", None)
    st.session_state["chat_history"] = []
    get_connector().close()  # stop a llama-server this session started
    if sso_mode() == "oidc":
        with contextlib.suppress(Exception):
            st.logout()
    st.rerun()


def _upload_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in Path(name).name)
    return (cleaned.strip("._") or "document")[:80]


def _receive_document(upload: Any, user: User, system: EcomopsSystem, history: list[dict[str, str]]) -> None:
    """Persist one upload, run the intake pipeline, and append the review message."""
    previous = st.session_state.get("doc_session")
    if previous is not None:
        discard_document(previous)
    st.session_state.pop("doc_session", None)
    st.session_state.pop("doc_output", None)

    folder = document_workdir()
    path = folder / _upload_name(getattr(upload, "name", "document"))
    path.write_bytes(upload.getvalue())
    try:
        with st.spinner("Reading the document…"):
            session = open_document(
                system, user, path, workdir=folder, filename=getattr(upload, "name", path.name)
            )
    except (DocumentError, ExtractionError, OcrUnavailable) as exc:
        history.append({"role": "user", "content": f"Attached **{path.name}**."})
        history.append({"role": "assistant", "content": f"I could not read that document: {exc}"})
        return
    st.session_state["doc_session"] = session
    history.append({"role": "user", "content": f"Attached **{session.job.filename}**."})
    history.append({"role": "assistant", "content": document_message(session)})


def _apply_chat_answers(system: EcomopsSystem, user: User, text: str) -> bool:
    """Apply ``key=value`` / ``Label: value`` lines typed in the chat.

    A question is never mistaken for an answer: the text must use an explicit
    separator and must parse to a field of the open document.
    """
    session = st.session_state.get("doc_session")
    if session is None or not ("=" in text or ":" in text):
        return False
    if not parse_answers(text, session.job):
        return False
    updated = answer_document(system, user, session, text)
    if not updated:
        return False
    st.session_state["doc_clear_answer"] = True
    flash(f"{len(updated)} field(s) updated from your message.")
    return True


def _render_document_panel(user: User, system: EcomopsSystem) -> None:
    session = st.session_state.get("doc_session")
    if session is None:
        return
    job = session.job

    if st.session_state.pop("doc_clear_answer", False):
        st.session_state.pop("doc_answer_text", None)

    st.markdown("<div class='msm-chat-head'>DOCUMENT</div>", unsafe_allow_html=True)
    st.caption(f"{job.filename} · {job.doc_type.label} · {job.pages} page{'s' if job.pages != 1 else ''}")

    for row in document_rows(job)[:14]:
        value = row["msm"] or "—"
        meta = row["source"] or row["status"]
        st.markdown(
            f"<div class='msm-doc-row'>"
            f"<span class='msm-doc-field'>{html.escape(str(row['field']))}</span>"
            f"<span class='msm-doc-value'>{html.escape(str(value))}</span>"
            f"<span class='msm-sub'>{html.escape(str(meta))}</span></div>",
            unsafe_allow_html=True,
        )

    if job.missing:
        st.text_input(
            "Answer gaps",
            key="doc_answer_text",
            placeholder="sku=SKU-1001",
            help="One key=value per line. Use key= to leave a field blank.",
        )
        if st.button("Apply answers", width="stretch"):
            updated = answer_document(system, user, session, st.session_state.get("doc_answer_text", ""))
            if updated:
                st.session_state["doc_clear_answer"] = True
                flash(f"{len(updated)} field(s) updated from your answers.")
            else:
                flash("No answers recognised — use the field key, e.g. `sku=SKU-1001`.")
            st.rerun()

    mode = st.radio(
        "Format",
        ["Original layout", "Re-flowed"],
        key="doc_mode",
        horizontal=True,
        help="Original keeps the page geometry; re-flowed rebuilds a clean, labelled document.",
    )
    if st.button("Generate document", width="stretch"):
        with st.spinner("Filling the document…"):
            output = finish_document(
                system, user, session, mode="reflow" if mode == "Re-flowed" else "original"
            )
        st.session_state["doc_output"] = str(output)
        st.rerun()

    output_path = st.session_state.get("doc_output")
    if output_path and Path(output_path).exists():
        with contextlib.suppress(Exception):
            st.image(preview_bytes(Path(output_path)), width="stretch")
        st.download_button(
            "Download filled PDF",
            data=Path(output_path).read_bytes(),
            file_name=Path(output_path).name,
            mime="application/pdf",
            width="stretch",
        )

    if st.button("Discard document", width="stretch"):
        discard_document(session)
        st.session_state.pop("doc_session", None)
        st.session_state.pop("doc_output", None)
        st.rerun()


# Primary navigation. Kept as pure data so the sidebar can render it before the
# view renderers are defined; the dispatch map is assembled at the bottom.
NAV_ITEMS: tuple[tuple[str, str, str, tuple[Permission, ...]], ...] = (
    ("overview", "Overview", ":material/dashboard:", (Permission.VIEW_OPERATIONS,)),
    ("request", "Customer request", ":material/edit_note:", (Permission.ACT_OPERATIONS,)),
    ("stock", "Stock clearance", ":material/inventory_2:", (Permission.ACT_OPERATIONS,)),
    ("transit", "Transit", ":material/local_shipping:", (Permission.VIEW_OPERATIONS,)),
    ("returns", "Returns", ":material/assignment_return:", (Permission.VIEW_OPERATIONS,)),
    ("approvals", "Approvals", ":material/fact_check:",
     (Permission.APPROVE_OPERATIONS, Permission.APPROVE_FINANCE)),
    ("trace", "Event trace", ":material/history:", (Permission.VIEW_AUDIT,)),
    ("inventory", "Inventory", ":material/warehouse:", (Permission.VIEW_OPERATIONS,)),
    ("finance", "Referrals & finance", ":material/payments:", (Permission.VIEW_FINANCE,)),
    ("tasks", "Tasks", ":material/task_alt:", (Permission.VIEW_OPERATIONS,)),
)


def _visible_nav(user: User) -> list[tuple[str, str, str]]:
    """Menu entries this role may open, in display order."""
    return [
        (key, label, icon)
        for key, label, icon, permissions in NAV_ITEMS
        if user.can_any(*permissions)
    ]


def _render_icon_bar(user: User) -> None:
    """Top-right icon strip: every workspace as an icon with a hover label."""
    items = _visible_nav(user)
    keys = {key for key, _, _ in items}
    if st.session_state.get("view") not in keys:
        st.session_state["view"] = items[0][0]
    # Streamlit's flex blocks resolve max-content to the full page width, so the
    # strip gets an explicit width derived from the number of visible items.
    strip_width = len(items) * 2.15 + max(len(items) - 1, 0) * 0.28 + 0.2
    st.markdown(
        f"<style>.st-key-msm_icon_bar, "
        f".st-key-msm_icon_bar [data-testid='stLayoutWrapper'], "
        f".st-key-msm_icon_bar [data-testid='stHorizontalBlock'] "
        f"{{width:{strip_width:.2f}rem !important;}}</style>",
        unsafe_allow_html=True,
    )
    with st.container(key="msm_icon_bar"):
        cols = st.columns(len(items), gap="small")
        for col, (key, label, icon) in zip(cols, items, strict=True):
            active = st.session_state.get("view") == key
            clicked = col.button(
                "",
                icon=icon,
                key=f"icon_{key}",
                help=label,
                type="primary" if active else "secondary",
                width="stretch",
            )
            if clicked and not active:
                st.session_state["view"] = key
                st.rerun()


def _render_sidebar(user: User, system: EcomopsSystem) -> None:
    with st.sidebar:
        st.markdown(
            "<div class='msm-brand'><span class='msm-mark'>◈</span> MSM</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<span class='msm-chip'>{user.label}</span> "
            f"<span class='msm-sub'>{user.name}</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"{user.email} · via {user.provider}")

        st.divider()
        show_chat = st.toggle("Assistant", value=False, key="show_chat")
        if show_chat:
            history = st.session_state.setdefault("chat_history", [])
            st.markdown(
                "<div class='msm-chat-head'>MSM Assistant</div>"
                "<div class='msm-chat-model'>Model · "
                f"{html.escape(get_connector().config.model_name or DEFAULT_LOCAL_MODEL)}</div>",
                unsafe_allow_html=True,
            )
            with st.container(height=240, key="chat_panel", autoscroll=bool(history)):
                if not history:
                    st.markdown(
                        "<div class='msm-chat-empty'>Ask about stock, approvals, shipments, "
                        "returns" + (" or finance." if user.can(Permission.VIEW_FINANCE) else ".")
                        + "</div>"
                        "<div class='msm-chat-hint'>Try “how many customer requests came?” "
                        "or “what is pending?”</div>",
                        unsafe_allow_html=True,
                    )
                for message in history:
                    st.chat_message(message["role"]).markdown(message["content"])
            prompt = st.chat_input(
                "Ask MSM or attach a document…",
                accept_file=True,
                file_type=["pdf", "png", "jpg", "jpeg"],
                key="chat_prompt",
            )
            if prompt:
                text = str(getattr(prompt, "text", prompt) or "").strip()
                files = list(getattr(prompt, "files", None) or [])
                if files:
                    _receive_document(files[0], user, system, history)
                    st.rerun()
                elif text:
                    if _apply_chat_answers(system, user, text):
                        history.append({"role": "user", "content": text})
                        history.append(
                            {
                                "role": "assistant",
                                "content": document_message(st.session_state["doc_session"]),
                            }
                        )
                    else:
                        history.append({"role": "user", "content": text})
                        with st.spinner("Thinking…"):
                            reply = OpsChat(
                                system,
                                user,
                                history=list(history),
                                document=st.session_state.get("doc_session"),
                            ).answer(text)
                        history.append({"role": "assistant", "content": reply})
                    st.rerun()
            if history and st.button("Clear chat", width="stretch"):
                st.session_state["chat_history"] = []
                st.rerun()
            _render_document_panel(user, system)

        st.divider()
        if user.can(Permission.MANAGE):
            with st.expander("Configuration"):
                st.radio(
                    "LLM backend",
                    ["Rule-based (deterministic)", "MLX (local model)"],
                    key="backend",
                    help="MLX runs a local model on Apple Silicon. Any load or generation "
                    "failure falls back to the rules automatically.",
                )
                st.text_input(
                    "MLX model repo id",
                    key="model",
                    disabled=st.session_state.get("backend") != "MLX (local model)",
                )
                st.checkbox(
                    "Merge grocery dataset",
                    key="grocery_enabled",
                    help="GroceryDataset.csv next to the demo SKUs — interface-test data, "
                    "not model training.",
                )
                if st.session_state.get("grocery_enabled"):
                    st.text_input("Grocery CSV", key="grocery_path")
                    st.number_input("Max products (0 = all)", min_value=0, step=50, key="grocery_limit")
                    if system.grocery_report is not None:
                        st.caption(system.grocery_report.label)
                    else:
                        st.warning("Grocery file not found — using the demo catalogue.")
                st.markdown("#### Inbox (IMAP)")
                st.caption(
                    "Read this operator's mailbox and turn email actions into tasks. "
                    "Credentials are kept in this browser session only."
                )
                st.text_input("IMAP host", key="mail_host", placeholder="imap.example.com")
                m1, m2 = st.columns([1, 2])
                m1.number_input("Port", min_value=1, max_value=65535, key="mail_port")
                m2.text_input("Username", key="mail_user")
                st.text_input("Password / app token", type="password", key="mail_password")
                m3, m4 = st.columns([2, 1])
                m3.text_input("Folder", key="mail_folder")
                m4.number_input("Messages", min_value=1, max_value=200, key="mail_limit")
                m5, m6 = st.columns(2)
                m5.checkbox("Use TLS (SSL)", key="mail_ssl")
                m6.checkbox("Mark as read", key="mail_mark_seen")

                st.markdown("#### Chat model")
                st.caption(
                    "Connect a local chat model, or drop in a `.gguf` file later. "
                    "The assistant falls back to deterministic answers when the "
                    "model is unavailable."
                )
                st.radio(
                    "Chat backend",
                    options=["off", "endpoint", "gguf"],
                    format_func=lambda mode: {
                        "off": "Rule-based only",
                        "endpoint": "Local endpoint (OpenAI-compatible)",
                        "gguf": "GGUF file (llama-server)",
                    }[mode],
                    key="chat_mode",
                )
                chat_mode = str(st.session_state.get("chat_mode") or "off")
                if chat_mode == "endpoint":
                    st.text_input("Endpoint", key="chat_base_url")
                    st.text_input(
                        "Model id (blank = discover)", key="chat_model"
                    )
                elif chat_mode == "gguf":
                    st.text_input("GGUF file path", key="chat_gguf")
                    c1, c2 = st.columns(2)
                    c1.number_input("Port", min_value=1, max_value=65535, key="chat_port")
                    c2.number_input("Context", min_value=512, step=512, key="chat_ctx")
                if chat_mode != "off":
                    st.text_input(
                        "API key", type="password", key="chat_api_key"
                    )
                cfg = _chat_config()
                st.caption(f"Target: `{cfg.endpoint}`")
                if st.button("Test chat model", width="stretch"):
                    connector = get_connector()
                    ok = connector.ensure_ready()
                    flash(
                        f"Chat model: {connector.status()}"
                        + (" — ready" if ok else " — using rule-based fallback")
                    )

                if st.button("Reset system", width="stretch"):
                    discard_document(st.session_state.get("doc_session"))
                    st.session_state.pop("doc_session", None)
                    st.session_state.pop("doc_output", None)
                    st.session_state.pop("system", None)
                    st.session_state.pop("cfg", None)
                    st.session_state["chat_history"] = []
                    flash("System reset — clean slate.")
                    st.rerun()

        with st.expander("About"):
            st.caption(
                "Four specialised agents share an in-process event bus with correlation "
                "and causation ids. Every side effect is proposed as an `ActionProposed` "
                "event and must clear the approval gate."
            )
            st.caption(f"Version {__version__} · audit: `ecomops replay --verify`")

        if st.button("Sign out", width="stretch"):
            _sign_out()


_render_sidebar(user, system)
_render_icon_bar(user)

# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #

backend_name = getattr(system.llm, "backend_name", "rule-based")
_active_key = st.session_state.get("view", "overview")
_active_label = next(
    (label for key, label, _icon, _perms in NAV_ITEMS if key == _active_key), "Overview"
)

message = st.session_state.pop("flash", None)
if message:
    st.info(message)

with st.container(key="msm_header"):
    st.markdown(
        "<div class='msm-eyebrow'>The Operations Atelier</div>", unsafe_allow_html=True
    )
    st.title("MSM")
    st.markdown(
        f"<div class='msm-page-head'>"
        f"<span class='msm-page-label'>{html.escape(_active_label)}</span>"
        f"<span class='msm-page-meta'>{html.escape(backend_name)} · "
        f"{html.escape(user.label)}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

ops_stats: list[tuple[str, str]] = []
if user.can(Permission.VIEW_OPERATIONS):
    ops_stats = [
        ("Events", f"{len(system.bus.history):,}"),
        ("Pending", f"{len(system.pending_approvals):,}"),
        ("Reservations", f"{len(system.bus.of_type('StockReserved')):,}"),
        ("Shipments", f"{len(system.bus.of_type('ShipmentRouted')):,}"),
        ("Discounts", f"{len(system.bus.of_type('DiscountApplied')):,}"),
        ("Returns", f"{len(system.bus.of_type('ReturnAssessed')):,}"),
    ]
finance_stats: list[tuple[str, str]] = []
if user.can(Permission.VIEW_FINANCE):
    snapshot = finance_snapshot(system)
    finance_stats = [
        ("Gross revenue", f"${snapshot['gross_revenue']:,.0f}"),
        ("Refunds", f"${snapshot['refund_total']:,.0f}"),
        ("Net revenue", f"${snapshot['net_revenue']:,.0f}"),
        ("Referral revenue", f"${snapshot['referral_revenue']:,.0f}"),
        ("Commission due", f"${snapshot['commission_due']:,.0f}"),
        ("Inventory value", f"${snapshot['inventory_value']:,.0f}"),
    ]
stats = ops_stats + finance_stats
# One keyed block for every KPI: the CSS turns it into a single dense rail so the
# header stats never push the tabs below the fold.
with st.container(key="msm_stat_rail"):
    cols = st.columns(max(len(stats), 1))
    for col, (label, value) in zip(cols, stats, strict=False):
        col.metric(label, value)


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #

def _event_count_frame(system: EcomopsSystem) -> pd.DataFrame:
    counts = pd.Series([record.topic for record in system.bus.history], dtype="object")
    if counts.empty:
        return pd.DataFrame({"topic": [], "events": []})
    frame = counts.value_counts().rename_axis("topic").reset_index(name="events")
    return frame.sort_values("events", ascending=False)


def _render_overview(user: User, system: EcomopsSystem) -> None:
    can_act = user.can(Permission.ACT_OPERATIONS)
    st.markdown("### Scenario")
    st.caption(
        "Customer request → intent → demand → reservation + shipment, then a warehouse "
        "clearance pass. Risky proposals wait in Approvals."
    )
    b1, b2, b3 = st.columns(3)
    if b1.button("Run demo", type="primary", width="stretch", disabled=not can_act):
        async def _demo(s: EcomopsSystem) -> None:
            await s.receive_customer_request("CUST-001", DEMO_TEXT)
            await s.report_stock_levels()

        with st.spinner("Running the scenario through bus + agents…"):
            run(_demo(system))
        pending = len(system.pending_approvals)
        flash(
            f"Scenario submitted — {len(system.bus.history)} events total. "
            + (f"{pending} proposal(s) await approval." if pending
               else "Everything was within policy.")
        )
        st.rerun()
    if b2.button("Carrier exception", width="stretch", disabled=not can_act):
        routed = system.bus.of_type("ShipmentRouted")
        if not routed:
            flash("No routed shipment yet — approve a dispatch proposal first.")
        else:
            with st.spinner("Carrier webhook → exception → resolution…"):
                run(system.report_delivery_exception(
                    routed[0].shipment_id, "carrier_delay", "hub strike in transit"
                ))
            flash(f"Exception reported for {routed[0].shipment_id}.")
        st.rerun()
    if b3.button("Sample history", width="stretch", disabled=not can_act,
                 help="Seed deterministic sample events for the Event trace tab."):
        with st.spinner("Seeding sample events…"):
            seed_sample_events(system, user)
        flash(f"Sample history loaded — {len(system.bus.history)} events on the bus.")
        st.rerun()

    st.markdown("### Snapshot")
    routed = system.bus.of_type("ShipmentRouted")
    pending = system.pending_approvals
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Units on hand", f"{sum(p.stock for p in system.inventory.products.values()):,}")
    s2.metric("SKUs", f"{len(system.inventory.products):,}")
    s3.metric("Routed shipments", len(routed))
    s4.metric("Awaiting approval", len(pending))
    if pending:
        st.caption("Next up: " + ", ".join(f"`{e.action}`" for e in pending[:3]))

    st.markdown("### Event flow")
    frame = _event_count_frame(system)
    if frame.empty:
        st.caption("No events yet — run the demo or load sample history.")
    elif alt is not None:
        chart = (
            alt.Chart(frame)
            .mark_bar(cornerRadiusEnd=2, color=_palette().gold)
            .encode(
                x=alt.X("events:Q", title=None),
                y=alt.Y("topic:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240)),
                tooltip=["topic", "events"],
            )
            .properties(height=max(200, 24 * len(frame)))
            .configure(background="transparent")
            .configure_axis(
                labelColor=_palette().muted, titleColor=_palette().muted,
                gridColor=_palette().tint_strong, domainColor=_palette().line,
            )
        )
        st.altair_chart(chart, width="stretch")
    else:  # pragma: no cover - altair ships with Streamlit
        st.bar_chart(frame.set_index("topic")["events"])

    if system.bus.history:
        timeline = pd.DataFrame([
            {"seq": record.seq, "time": record.event.ts, "topic": record.topic}
            for record in system.bus.history
        ])
        timeline["events"] = range(1, len(timeline) + 1)
        if alt is not None:
            line = (
                alt.Chart(timeline)
                .mark_line(interpolate="monotone", color=_palette().accent_2, strokeWidth=1.4)
                .encode(
                    x=alt.X("time:T", title=None),
                    y=alt.Y("events:Q", title="cumulative events"),
                    tooltip=["time:T", "topic:N"],
                )
                .properties(height=180)
                .configure(background="transparent")
                .configure_axis(
                    labelColor=_palette().muted, titleColor=_palette().muted,
                    gridColor=_palette().tint_strong, domainColor=_palette().line,
                )
            )
            st.altair_chart(line, width="stretch")
    if routed:
        latest = next(reversed(routed))
        st.caption(
            f"Latest shipment `{latest.shipment_id}` · {latest.quantity} x {latest.sku} "
            f"→ {latest.destination} via {latest.plan.carrier}"
        )


# --------------------------------------------------------------------------- #
# Customer request
# --------------------------------------------------------------------------- #

def _render_request(user: User, system: EcomopsSystem) -> None:
    with st.form("request_form"):
        st.markdown("### Submit a customer request")
        cols = st.columns([1, 3])
        customer_id = cols[0].text_input("Customer id", value="CUST-001")
        request_text = cols[1].text_area(
            "Request text", value=DEMO_TEXT, height=80,
            help="Free text — the agent extracts intent, items, destination and urgency.",
        )
        submitted = st.form_submit_button("Run pipeline", type="primary")

    if submitted:
        with st.spinner("Intent capture → demand → clearance → routing…"):
            run(system.receive_customer_request(customer_id, request_text))
        flash(f"Request from {customer_id} processed — see the Event trace tab.")
        st.rerun()

    captured = system.bus.last("CustomerIntentCaptured")
    if captured:
        st.markdown("### Last captured intent")
        a, b, c, d = st.columns(4)
        a.metric("Intent", captured.intent.intent_type)
        b.metric("Destination", captured.intent.destination or "—")
        c.metric("Urgency", captured.intent.urgency)
        d.metric("Fully matched", "yes" if captured.matched else "no")
        st.caption(f"resolved by `{captured.llm_backend}`")
        if captured.intent.items:
            st.dataframe(
                pd.DataFrame([
                    {"query": i.query, "sku": i.sku or "—", "product": i.product_name or "—", "qty": i.quantity}
                    for i in captured.intent.items
                ]),
                hide_index=True, width="stretch",
            )
        if captured.intent.summary:
            st.caption(captured.intent.summary)

    clarification = system.bus.last("IntentNeedsClarification")
    if clarification:
        st.warning(
            f"Agent asked the customer: **{clarification.question}** "
            f"(unresolved: {', '.join(clarification.unresolved_queries)})"
        )


# --------------------------------------------------------------------------- #
# Tasks (inbox + customer form extraction)
# --------------------------------------------------------------------------- #


def _read_inbox(user: User, system: EcomopsSystem) -> None:
    """Pull the configured mailbox, then let the task agent do the extraction."""
    config = _mail_config()
    if not config.configured:
        flash("Inbox not configured — add the IMAP host, username and password in Configuration.")
        return
    try:
        messages = mail.fetch(config)
    except mail.MailError as exc:
        flash(f"Inbox read failed: {exc}")
        return
    before = len(system.bus.of_type("TaskExtracted"))
    for message in messages:
        run(system.receive_email(
            sender=message.sender,
            body=message.body,
            subject=message.subject,
            recipients=list(message.recipients),
            message_id=message.message_id,
            assignee_email=user.email,
        ))
    added = len(system.bus.of_type("TaskExtracted")) - before
    flash(f"Inbox: {len(messages)} message(s) read → {added} task(s) added.")


def _render_tasks(user: User, system: EcomopsSystem) -> None:
    st.markdown("### My tasks")
    st.caption(
        "Action items extracted from your email inbox and customer forms. "
        "Deterministic rules run offline; a configured local model can refine them."
    )
    controls = st.columns([1, 1, 1.4])
    show_all = controls[0].checkbox(
        "All assignees", key="tasks_all",
        disabled=not user.can(Permission.MANAGE),
        help="Managers can see tasks assigned to everyone.",
    )
    chosen_status = controls[1].selectbox(
        "Show", ["All", "Open", "In progress", "Done"], key="tasks_status"
    )
    if controls[2].button("Read inbox", width="stretch", key="tasks_read_inbox"):
        _read_inbox(user, system)
        st.rerun()

    tasks = system.my_tasks(user.email, all_users=show_all)
    counts = task_counts(tasks)
    st.caption(
        f"**{counts['open']}** open · **{counts['in_progress']}** in progress · "
        f"**{counts['done']}** done"
    )
    wanted = {
        "All": None, "Open": "open", "In progress": "in_progress", "Done": "done",
    }[chosen_status]
    visible = [task for task in tasks if wanted is None or task.status == wanted]
    if not visible:
        st.caption("No tasks here yet — read your inbox or submit a customer form below.")
    for task in visible:
        with st.container(key=f"task_{task.task_id}"):
            badge = (
                f"<span class='msm-task-badge' data-p='{task.priority}'>{task.priority}</span>"
                f"<span class='msm-task-badge'>{task.source}</span>"
            )
            st.markdown(
                f"<div class='msm-task-card'>"
                f"<div class='msm-task-title'>{badge}{html.escape(task.title)}</div>"
                f"<div class='msm-task-meta'>"
                + (f"<b>Due</b> {html.escape(str(task.due_date))} · " if task.due_date else "")
                + f"<b>Assigned to</b> {html.escape(task.assignee_email)}"
                + (f" · <b>From</b> {html.escape(task.sender)}" if task.sender else "")
                + f"<br><b>Status</b> {html.escape(task.status.replace('_', ' '))}"
                + f" · <b>ID</b> {html.escape(task.task_id)}"
                + ("</div></div>"),
                unsafe_allow_html=True,
            )
            if task.description:
                st.caption(task.description)
            new_status = st.selectbox(
                "Status", TASK_STATUSES,
                index=TASK_STATUSES.index(task.status) if task.status in TASK_STATUSES else 0,
                key=f"task_status_{task.task_id}",
                label_visibility="collapsed",
            )
            if new_status != task.status:
                try:
                    system.update_task_status(
                        task.task_id, new_status, changed_by=user.email
                    )
                except TaskError as exc:
                    flash(str(exc))
                st.rerun()

    if user.can(Permission.ACT_OPERATIONS):
        with st.expander("Create tasks from a customer form"):
            st.caption(
                "The new customer form pipeline — same extractor as mail, "
                "no inbox connection needed."
            )
            with st.form("task_form"):
                text = st.text_area(
                    "Form text", height=90, key="task_form_text",
                    placeholder="Please review the order by 2026-11-25\n- [ ] Confirm the delivery",
                )
                who = st.columns(2)
                assignee = who[0].text_input("Assign to", value=user.email, key="task_form_assignee")
                submitted_by = who[1].text_input(
                    "Submitted by", value=user.email, key="task_form_by"
                )
                submitted = st.form_submit_button("Extract tasks", type="primary")
            if submitted:
                if not text.strip():
                    flash("Add some form text first.")
                else:
                    before = len(system.bus.of_type("TaskExtracted"))
                    run(system.receive_form(
                        text=text, submitted_by=submitted_by, assignee_email=assignee
                    ))
                    added = len(system.bus.of_type("TaskExtracted")) - before
                    flash(f"Form processed — {added} task(s) added.")
                    st.rerun()


# --------------------------------------------------------------------------- #
# Stock clearance
# --------------------------------------------------------------------------- #

_ADVERT_SIZES = {"Portrait 4:5": PORTRAIT, "Square 1:1": SQUARE}


def _slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "poster"


def _advert_candidates(system: EcomopsSystem) -> list[dict[str, Any]]:
    """Items that can go on a poster: applied discounts first, then proposals, then stock."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(sku: str, old: float, new: float, status: str) -> None:
        product = system.inventory.products.get(sku)
        if product is None or sku in seen:
            return
        seen.add(sku)
        candidates.append({
            "sku": sku, "name": product.name, "old": round(float(old), 2),
            "new": round(float(new), 2), "category": product.category,
            "currency": product.currency, "status": status,
        })

    for event in reversed(system.bus.of_type("DiscountApplied")):
        add(event.sku, event.previous_price, event.new_price, "discount applied")
    for proposal in reversed(system.bus.of_type("ClearanceStrategyProposed")):
        product = system.inventory.products.get(proposal.strategy.sku)
        if product is None:
            continue
        new = round(product.unit_price * (1 - proposal.strategy.discount_pct / 100), 2)
        add(proposal.strategy.sku, product.unit_price, new, "proposed")
    for product in sorted(system.inventory.products.values(), key=lambda p: p.name):
        add(product.sku, product.unit_price, product.unit_price, "list price")
    return candidates


def _render_advert(system: EcomopsSystem, user: User) -> None:
    """Advertisement poster builder: 1-5 items, web photos, download and push."""
    candidates = _advert_candidates(system)
    if not candidates:
        st.info("No catalogue items are loaded, so there is nothing to advertise yet.")
        return

    labels = {c["sku"]: f"{c['sku']} · {c['name']} · {c['status']}" for c in candidates}
    names = {c["sku"]: c["name"] for c in candidates}
    keys = [c["sku"] for c in candidates]
    can_send = user.can(Permission.ACT_OPERATIONS)
    # Seed the widgets instead of passing defaults: a key that exists in session
    # state (a row picked in the table below) must not also get an index/value.
    if st.session_state.get("advert_sku") not in keys:
        st.session_state["advert_sku"] = keys[0]
    st.session_state.setdefault("advert_company", "MSM")

    with st.container(border=True):
        head, close = st.columns([6, 1])
        head.markdown("### Advertisement generator")
        head.caption(
            "Pick up to five items, confirm the prices, pull photos from the web, "
            "then download the poster or push it to customers."
        )
        if close.button("Close", key="advert_close", type="tertiary"):
            st.session_state["advert_open"] = False
            st.rerun()

        sku = st.selectbox(
            "Item for offer", keys, key="advert_sku",
            format_func=lambda key: labels[key],
        )
        chosen = next(item for item in candidates if item["sku"] == sku)

        extras = st.multiselect(
            "Also on this poster", [key for key in keys if key != sku],
            max_selections=MAX_PHOTOS - 1, key=f"advert_extras_{sku}",
            format_func=lambda key: labels[key], placeholder="Up to four more items",
            help="Select more items and the poster lays their photos out as a collage.",
        )
        selected = [sku, *extras]

        price_cols = st.columns([2.2, 1, 1])
        old_key, new_key = f"advert_old_{sku}", f"advert_new_{sku}"
        st.session_state.setdefault(old_key, float(chosen["old"]))
        st.session_state.setdefault(new_key, float(chosen["new"]))
        company = price_cols[0].text_input("Company name", key="advert_company")
        old_price = price_cols[1].number_input(
            "Old price", min_value=0.01, step=0.5, format="%.2f", key=old_key,
        )
        new_price = price_cols[2].number_input(
            "New price", min_value=0.01, step=0.5, format="%.2f", key=new_key,
        )
        pct = (1 - new_price / old_price) * 100 if old_price else 0.0
        st.caption(
            f"Discount **{pct:.0f}%** · save {chosen['currency']}"
            f"{max(old_price - new_price, 0):,.2f} · {chosen['status']}"
        )

        media_cols = st.columns([2.2, 1, 1])
        uploads = media_cols[0].file_uploader(
            "Item photos (optional)", type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True, key="advert_images",
            help=(
                "One file per item, in the order above. Anything left out is "
                "downloaded from the web when the box below is ticked."
            ),
        )
        tone = media_cols[1].selectbox(
            "Tone", [name.title() for name in TONES], key="advert_tone",
        )
        size_label = media_cols[2].selectbox("Format", list(_ADVERT_SIZES), key="advert_size")

        fetch_web = st.checkbox(
            "Download item photos from the web", value=True, key="advert_fetch_web",
            help="Uses Wikimedia Commons / Openverse; anything not found falls back to a monogram card.",
        )
        use_model = st.checkbox(
            "Ask the assistant for the headline", value=False, key="advert_use_model",
            help="Uses the same LLM layer as the agents; falls back to the rule-based copy.",
        )

        if st.button("Generate poster", type="primary", key="advert_generate"):
            files = list(uploads or [])
            photos: list[Photo] = []
            provenances: list[str] = []
            with st.spinner("Assembling item photos…"):
                for index, item_sku in enumerate(selected):
                    item_name = names[item_sku]
                    if index < len(files):
                        photos.append(Photo(item_name, files[index].getvalue(), "upload"))
                        provenances.append(f"{item_name}: upload")
                        continue
                    fetched = fetch_item_image(item_name) if fetch_web else None
                    if fetched is not None:
                        photos.append(Photo(item_name, fetched.image, fetched.source))
                        provenances.append(f"{item_name}: {describe_image(fetched)}")
                    else:
                        photos.append(Photo(item_name, None, ""))
                        provenances.append(f"{item_name}: monogram card")

            try:
                offer = Offer(
                    company=company,
                    product_name=chosen["name"],
                    old_price=float(old_price),
                    new_price=float(new_price),
                    sku=chosen["sku"],
                    currency=chosen["currency"],
                    category=chosen["category"],
                    image=photos[0].image if photos else None,
                    tone=tone.lower(),
                    photos=tuple(photos),
                )
            except AdvertError as exc:
                st.error(str(exc))
            else:
                copy = build_copy(offer, tone=tone.lower())
                backend = "rule-based"
                if use_model:
                    try:
                        result = run(system.llm.complete(
                            task="write_advert_copy",
                            instructions=(
                                "Write social-media advertisement copy for a clearance offer. "
                                "Return JSON {badge, headline, subheadline, tagline, cta}."
                            ),
                            payload={
                                "company": offer.company,
                                "product_name": offer.product_name,
                                "old_price": offer.old_price,
                                "new_price": offer.new_price,
                                "currency": offer.currency,
                                "category": offer.category,
                                "tone": tone.lower(),
                            },
                        ))
                        copy = AdvertCopy.from_payload(result.data, offer=offer)
                        backend = result.backend
                    except Exception:
                        copy = build_copy(offer, tone=tone.lower())
                st.session_state["advert_offer"] = offer
                st.session_state["advert_poster"] = poster_bytes(
                    offer, copy, size=_ADVERT_SIZES[size_label],
                )
                st.session_state["advert_caption"] = copy.caption(offer)
                st.session_state["advert_file"] = (
                    f"{_slug(offer.company)}-{_slug(offer.product_name)}-{_slug(size_label)}.png"
                )
                st.session_state["advert_backend"] = backend
                st.session_state["advert_price_line"] = (
                    f"{offer.price(offer.old_price)} → {offer.price(offer.new_price)}"
                )
                st.session_state["advert_photo_lines"] = provenances
                st.session_state["advert_report"] = None

        poster = st.session_state.get("advert_poster")
        offer = st.session_state.get("advert_offer")
        if poster and offer is not None:
            preview, details = st.columns([1, 1])
            with preview:
                st.image(poster, width="stretch")
                st.download_button(
                    "Download poster", data=poster,
                    file_name=st.session_state.get("advert_file", "poster.png"),
                    mime="image/png", type="primary", width="stretch",
                )
            with details:
                st.caption(
                    f"Copy by `{st.session_state.get('advert_backend', 'rule-based')}` · "
                    f"{st.session_state.get('advert_price_line', '')} · "
                    f"{len(offer.gallery)} item(s)"
                )
                st.text_area(
                    "Social caption", value=st.session_state.get("advert_caption", ""),
                    height=170, help="Paste this next to the image on the post.",
                )

                st.markdown("**Push to customers**")
                send_cols = st.columns([1, 1])
                channel = send_cols[0].selectbox(
                    "Channel", list(CHANNELS), key="advert_channel",
                    format_func=str.title,
                )
                if send_cols[1].button(
                    "Push poster", key="advert_push", width="stretch",
                    icon=":material/send:", disabled=not can_send,
                    help=(
                        "Records a PromotionPublished event for every customer on file."
                        if can_send else "Your role cannot publish campaigns."
                    ),
                ):
                    report = publish_promotion(
                        system.bus, offer,
                        caption=st.session_state.get("advert_caption", ""),
                        channel=channel, published_by=user.email,
                        image_bytes=len(poster),
                    )
                    st.session_state["advert_report"] = report

                report = st.session_state.get("advert_report")
                if report is not None:
                    st.success(report.summary())
                    st.caption(
                        "Recipients: " + ", ".join(report.recipients)
                        + f" · event `{report.event_id}` on the bus"
                    )

                lines = st.session_state.get("advert_photo_lines") or []
                if lines:
                    st.caption("Photos — " + " · ".join(lines))
                st.caption(
                    "Tip: 4:5 fills the feed on Instagram and Facebook; 1:1 survives grid crops."
                )


def _render_stock(user: User, system: EcomopsSystem) -> None:
    with st.form("stock_form"):
        st.markdown("### Proactive overstock clearance")
        cols = st.columns(2)
        min_age = cols[0].slider("Min age (days)", 0, 365, 90)
        min_stock = cols[1].slider("Min stock units", 0, 2000, 100)
        actions = st.columns([1.15, 1])
        stock_go = actions[0].form_submit_button(
            "Analyse warehouse stock", type="primary", width="stretch",
        )
        advert_go = actions[1].form_submit_button(
            "Adv generator", icon=":material/campaign:", width="stretch",
            help="Build a social-media poster for one of the proposed offers.",
        )

    if advert_go:
        # No st.rerun(): the generator renders further down this same run, and a rerun
        # would bounce the user back to the first tab.
        st.session_state["advert_open"] = True

    if stock_go:
        with st.spinner("Stock report → overstock analysis → clearance strategies…"):
            run(system.report_stock_levels(min_age_days=min_age, min_stock=min_stock))
        n = len(system.bus.of_type("ClearanceStrategyProposed"))
        # No st.rerun(): the strategy and discount tables render below in this same
        # run, and a rerun would drop the user back on the first tab.
        st.success(f"Stock report processed — {n} clearance strategy event(s) on the bus.")

    strategies = system.bus.of_type("ClearanceStrategyProposed")
    if strategies:
        st.markdown("### Strategies proposed")
        rows = [
            {"sku": s.strategy.sku, "discount %": s.strategy.discount_pct,
             "bundle": ", ".join(s.strategy.bundle_with) or "—",
             "trigger": s.trigger, "rationale": s.strategy.rationale,
             "backend": s.llm_backend}
            for s in strategies
        ]
        table = st.dataframe(
            pd.DataFrame(rows),
            hide_index=True, width="stretch",
            on_select="rerun", selection_mode="single-row",
        )
        picked = list(getattr(getattr(table, "selection", None), "rows", []) or [])
        if picked:
            row = rows[picked[0]]
            st.session_state["advert_sku"] = row["sku"]
            st.caption(
                f"Selected `{row['sku']}` — open **Adv generator** to build its poster."
            )
        discounts = system.bus.of_type("DiscountApplied")
        if discounts:
            st.markdown("### Discounts applied")
            st.dataframe(
                pd.DataFrame([
                    {"sku": d.sku, "discount %": d.discount_pct,
                     "was": f"${d.previous_price:.2f}", "now": f"${d.new_price:.2f}"}
                    for d in discounts
                ]),
                hide_index=True, width="stretch",
            )
    else:
        st.caption("No clearance run yet — the pass triggers the Stock Clearance Agent on overstocked SKUs.")

    # Rendered after the tables so a row selected there is the offer the generator opens on.
    if st.session_state.get("advert_open"):
        st.divider()
        _render_advert(system, user)


# --------------------------------------------------------------------------- #
# Transit
# --------------------------------------------------------------------------- #

def _render_transit_notice(system: EcomopsSystem, user: User) -> None:
    """Shipping notice builder: one shipment, one customer, one poster."""
    shipments = system.shipments()
    if not shipments:
        st.info("No shipments to notify about yet.")
        return

    keys = list(shipments.keys())
    if st.session_state.get("transit_shipment") not in keys:
        st.session_state["transit_shipment"] = keys[0]
    st.session_state.setdefault("transit_company", "MSM")
    can_send = user.can(Permission.ACT_OPERATIONS)

    with st.container(border=True):
        head, close = st.columns([6, 1])
        head.markdown("### Customer shipping notice")
        head.caption(
            "Pick a shipment, confirm the details, and the poster tells that one customer "
            "what is moving, where to, and when it lands."
        )
        if close.button("Close", key="transit_notice_close", type="tertiary"):
            st.session_state["transit_notice_open"] = False
            st.rerun()

        shipment_id = st.selectbox("Shipment for the notice", keys, key="transit_shipment")
        ctx = shipments[shipment_id]
        product = system.inventory.products.get(ctx["sku"])
        product_name = product.name if product else ctx["sku"]
        plan = ctx["plan"]

        fact_cols = st.columns([1.1, 1.1, 2])
        company = fact_cols[0].text_input("Company name", key="transit_company")
        channel = fact_cols[1].selectbox(
            "Channel", list(CHANNELS), key="transit_channel", format_func=str.title,
        )
        fact_cols[2].caption(
            f"**{ctx['order_id']}** · customer **{ctx['customer_id']}** · "
            f"{ctx['quantity']} x {product_name}\n\n"
            f"{plan['carrier']} {plan['service_level']} · ETA {plan['eta_days']:g} day(s) "
            f"· {ctx['destination']}"
        )

        media_cols = st.columns([1, 1, 2])
        tone = media_cols[0].selectbox("Tone", [name.title() for name in TONES], key="transit_tone")
        size_label = media_cols[1].selectbox("Format", list(_ADVERT_SIZES), key="transit_size")
        fetch_web = media_cols[2].checkbox(
            "Download the item photo from the web", value=True, key="transit_fetch_web",
            help="Falls back to a monogram card when nothing usable is found.",
        )

        if st.button("Generate notice", type="primary", key="transit_generate"):
            photo = Photo(product_name, None, "")
            provenance = f"{product_name}: monogram card"
            with st.spinner("Assembling the item photo…"):
                if fetch_web:
                    fetched = fetch_item_image(product_name)
                    if fetched is not None:
                        photo = Photo(product_name, fetched.image, fetched.source)
                        provenance = f"{product_name}: {describe_image(fetched)}"
            try:
                notice = Notice(
                    company=company,
                    shipment_id=shipment_id,
                    order_id=ctx["order_id"],
                    customer_id=ctx["customer_id"],
                    destination=ctx["destination"],
                    carrier=plan["carrier"],
                    service_level=plan["service_level"],
                    eta_days=float(plan["eta_days"]),
                    items=(photo,),
                    quantity=int(ctx["quantity"]),
                    tone=tone.lower(),
                )
            except AdvertError as exc:
                st.error(str(exc))
            else:
                copy = build_notice_copy(notice, tone=tone.lower())
                st.session_state["transit_poster"] = notice_bytes(
                    notice, copy, size=_ADVERT_SIZES[size_label],
                )
                st.session_state["transit_caption"] = notice.caption()
                st.session_state["transit_file"] = (
                    f"{_slug(company)}-{_slug(shipment_id)}-{_slug(size_label)}.png"
                )
                st.session_state["transit_notice_obj"] = notice
                st.session_state["transit_photo_line"] = provenance
                st.session_state["transit_report"] = None

        poster = st.session_state.get("transit_poster")
        notice = st.session_state.get("transit_notice_obj")
        if poster and notice is not None:
            preview, details = st.columns([1, 1])
            with preview:
                st.image(poster, width="stretch")
                st.download_button(
                    "Download notice", data=poster,
                    file_name=st.session_state.get("transit_file", "notice.png"),
                    mime="image/png", type="primary", width="stretch",
                )
            with details:
                st.caption(
                    f"{notice.shipment_id} · {notice.carrier} {notice.service_level} · "
                    f"ETA {notice.eta_label.lower()} · to {notice.destination}"
                )
                st.text_area(
                    "Message to the customer",
                    value=st.session_state.get("transit_caption", ""),
                    height=150, help="Sent with the image on the chosen channel.",
                )
                if st.button(
                    "Send to customer", key="transit_send", type="primary",
                    icon=":material/send:", width="stretch", disabled=not can_send,
                    help=(
                        "Records a TransitNoticeSent event for this shipment's customer."
                        if can_send else "Your role cannot message customers."
                    ),
                ):
                    report = publish_transit_notice(
                        system.bus, notice,
                        caption=st.session_state.get("transit_caption", ""),
                        channel=channel, sent_by=user.email,
                    )
                    st.session_state["transit_report"] = report

                report = st.session_state.get("transit_report")
                if report is not None:
                    st.success(report.summary())
                    st.caption(f"Event `{report.event_id}` recorded on the bus.")

                line = st.session_state.get("transit_photo_line")
                if line:
                    st.caption(f"Photo — {line}")


def _render_transit(user: User, system: EcomopsSystem) -> None:
    st.markdown("### Shipments")
    shipments = system.shipments()
    show_cost = user.can(Permission.VIEW_FINANCE)
    if shipments:
        rows = []
        for sid, ctx in shipments.items():
            row = {"shipment": sid,
                   "order": ctx["order_id"], "sku": ctx["sku"], "qty": ctx["quantity"],
                   "customer": ctx["customer_id"], "destination": ctx["destination"],
                   "carrier": ctx["plan"]["carrier"], "service": ctx["plan"]["service_level"],
                   "eta (days)": ctx["plan"]["eta_days"], "risk": ctx["plan"]["risk"]}
            if show_cost:
                row["cost"] = f"${ctx['plan']['cost']:.2f}"
            rows.append(row)
        table = st.dataframe(
            pd.DataFrame(rows), hide_index=True, width="stretch",
            on_select="rerun", selection_mode="single-row",
        )
        picked = list(getattr(getattr(table, "selection", None), "rows", []) or [])
        if picked:
            st.session_state["transit_shipment"] = rows[picked[0]]["shipment"]
            st.caption(
                f"Selected `{rows[picked[0]]['shipment']}` — open **Customer notice** "
                "to tell that customer it is on the way."
            )
        notice_cols = st.columns([1, 1, 2])
        if notice_cols[0].button(
            "Customer notice", icon=":material/local_shipping:", width="stretch",
            key="transit_notice_open_btn",
        ):
            st.session_state["transit_notice_open"] = True
        notice_cols[1].caption("Poster + message, sent to one customer.")
    else:
        st.info("No shipments yet — grant a `dispatch_shipment` proposal in Approvals.")

    with st.form("exception_form"):
        st.markdown("### Report a delivery exception")
        ids = list(shipments.keys())
        cols = st.columns([1, 1, 2])
        ship_id = cols[0].selectbox("Shipment", ids if ids else ["—"], disabled=not ids)
        exc = cols[1].selectbox("Exception", [e.value for e in ExceptionType])
        detail = cols[2].text_input("Detail", value="hub strike in transit")
        exc_go = st.form_submit_button("Send carrier webhook", type="primary", disabled=not ids)

    if exc_go and ids:
        with st.spinner("Carrier webhook → exception → resolution proposal…"):
            run(system.report_delivery_exception(ship_id, exc, detail))
        flash(f"Exception `{exc}` reported for {ship_id}.")
        st.rerun()

    resolutions = system.bus.of_type("DeliveryExceptionResolved")
    if resolutions:
        st.markdown("### Resolutions")
        rows = []
        for r in resolutions:
            row = {"shipment": r.shipment_id, "action": r.resolution.action,
                   "carrier": r.resolution.carrier or "—",
                   "eta (days)": r.resolution.eta_days if r.resolution.eta_days is not None else "—",
                   "decided by": r.decided_by, "rationale": r.resolution.rationale}
            if show_cost:
                row["cost"] = f"${r.resolution.cost:.2f}"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if st.session_state.get("transit_notice_open"):
        st.divider()
        _render_transit_notice(system, user)


# --------------------------------------------------------------------------- #
# Returns
# --------------------------------------------------------------------------- #

def _render_returns(user: User, system: EcomopsSystem) -> None:
    st.markdown("### Returns & refunds")
    show_money = user.can(Permission.VIEW_FINANCE)
    shipments = system.shipments()
    if shipments:
        first_id, ctx = next(iter(shipments.items()))
        product = system.inventory.products[ctx["sku"]]
        st.caption(
            f"Simulate a damaged-item return against {first_id} "
            f"({ctx['quantity']} x {product.name}, customer {ctx['customer_id']})."
        )
        if st.button("Report damaged item", type="primary"):
            text = f"I want a refund for the {product.name}, it arrived damaged"
            with st.spinner("Return intent → assessment → gated refund…"):
                run(system.receive_customer_request(ctx["customer_id"], text))
            flash("Return assessed — a refund proposal is waiting in Approvals.")
            st.rerun()
    else:
        st.info("No delivered shipment yet — route one first, then simulate a return.")

    assessed = system.bus.of_type("ReturnAssessed")
    if assessed:
        st.markdown("### Assessments")
        rows = []
        for a in assessed:
            row = {"customer": a.customer_id, "sku": a.sku, "qty": a.quantity,
                   "action": a.assessment.action, "eligible": a.assessment.eligible,
                   "rationale": a.assessment.rationale, "backend": a.llm_backend}
            if show_money:
                row["refund"] = f"${a.assessment.refund_amount:.2f}"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    refunds = system.bus.of_type("RefundIssued")
    replacements = system.bus.of_type("ReplacementAuthorised")
    rejections = system.bus.of_type("ReturnRejected")
    if refunds:
        st.markdown("### Refunds issued")
        rows = []
        for r in refunds:
            row = {"customer": r.customer_id, "order": r.order_id or "—", "sku": r.sku,
                   "qty": r.quantity, "restocked": r.restocked, "decided by": r.decided_by}
            if show_money:
                row["amount"] = f"${r.amount:.2f}"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if replacements:
        st.markdown("### Replacements authorised")
        st.dataframe(
            pd.DataFrame([
                {"customer": r.customer_id, "order": r.order_id or "—", "sku": r.sku,
                 "qty": r.quantity, "decided by": r.decided_by}
                for r in replacements
            ]),
            hide_index=True, width="stretch",
        )
    if rejections:
        st.markdown("### Rejected returns")
        st.dataframe(
            pd.DataFrame([
                {"customer": r.customer_id, "sku": r.sku, "reason": r.reason,
                 "decided by": r.decided_by}
                for r in rejections
            ]),
            hide_index=True, width="stretch",
        )

    needs_order = system.bus.last("ReturnNeedsOrder")
    if needs_order:
        st.warning(needs_order.question)


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #

def _render_approvals(user: User, system: EcomopsSystem) -> None:
    pending = system.pending_approvals
    visible = [e for e in pending if can_approve(user, e)]
    hidden = len(pending) - len(visible)

    if not visible:
        st.success("Nothing for your role to approve — the queue is empty or reserved for finance.")
        if hidden:
            st.caption(f"{hidden} proposal(s) are visible only to another approval role.")
        return

    a, b = st.columns([1, 4])
    if a.button("Grant all", type="primary"):
        n = settle(system, granted=True, user=user)
        flash(f"Granted {n} proposal(s).")
        st.rerun()
    if b.button("Deny all"):
        n = settle(system, granted=False, user=user)
        flash(f"Denied {n} proposal(s).")
        st.rerun()

    for event in visible:
        risk = getattr(event.risk, "value", str(event.risk))
        color = RISK_COLORS.get(risk, "#333333")
        with st.container(border=True):
            head, badge = st.columns([4, 1])
            head.markdown(f"**`{event.action}`** · `{event.proposal_id}`")
            badge.markdown(
                f"<span style='color:{color};font-weight:bold;text-transform:uppercase'>"
                f"● {html.escape(str(risk))} risk</span>",
                unsafe_allow_html=True,
            )
            st.caption(event.reason)
            with st.expander("Parameters"):
                st.json(event.parameters)
            g, d = st.columns(2)
            if g.button("Grant", key=f"grant_{event.proposal_id}", width="stretch"):
                run(system.decide(event.proposal_id, granted=True, decided_by=user.email))
                flash(f"Granted `{event.action}` ({event.proposal_id}).")
                st.rerun()
            if d.button("Deny", key=f"deny_{event.proposal_id}", width="stretch"):
                run(system.decide(event.proposal_id, granted=False, decided_by=user.email))
                flash(f"Denied `{event.proposal_id}` — rejected by operator.")
                st.rerun()

    if hidden:
        st.caption(f"{hidden} proposal(s) are outside your approval scope.")


# --------------------------------------------------------------------------- #
# Event trace
# --------------------------------------------------------------------------- #

def _render_trace(user: User, system: EcomopsSystem) -> None:
    can_act = user.can(Permission.ACT_OPERATIONS)
    top = st.columns([1, 3])
    if top[0].button("Load sample events", disabled=not can_act):
        with st.spinner("Seeding deterministic sample events…"):
            seed_sample_events(system, user)
        flash(f"Sample events loaded — {len(system.bus.history)} on the bus.")
        st.rerun()
    top[1].caption(
        "Sample history is generated locally by the demo scenario — no model, no network."
    )

    history = system.bus.history
    if not history:
        st.caption("No events yet. Load sample events above, or run the demo in Overview.")
        return

    all_topics = sorted({record.topic for record in history})
    f1, f2 = st.columns([1, 2])
    chosen = f1.multiselect("Topics", all_topics, default=all_topics)
    needle = f2.text_input("Search", placeholder="sku, customer id, order id…")
    needle = needle.lower().strip()

    records = []
    for record in history:
        if record.topic not in chosen:
            continue
        if needle and needle not in json.dumps(record.event.model_dump(mode="json")).lower():
            continue
        records.append(record)

    if not records:
        st.caption("No events match the filter.")
        return

    summaries = {seq: summary for seq, _, _, summary in system.bus.trace()}
    frame = pd.DataFrame([
        {"#": r.seq, "time": r.event.ts.strftime("%H:%M:%S"), "topic": r.topic,
         "source": r.event.source, "correlation": (r.event.correlation_id or "")[:12],
         "details": summaries.get(r.seq, "")[:110]}
        for r in records
    ])
    selection = st.dataframe(
        frame, hide_index=True, width="stretch",
        selection_mode="single-row", on_select="rerun",
    )
    rows = (selection or {}).get("selection", {}).get("rows", [])
    if rows:
        record = records[rows[0]]
        st.markdown(f"**Event #{record.seq} — {record.topic}**")
        st.json(record.event.model_dump(mode="json"))


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

def _render_inventory(user: User, system: EcomopsSystem) -> None:
    report = system.grocery_report
    if report is not None:
        st.caption(f"Grocery pack · {report.label}")
    products = list(system.inventory.products.values())

    f1, f2, f3 = st.columns([2, 1, 1])
    search = f1.text_input(
        "Search products", "", key="inv_search",
        placeholder="e.g. peanut butter, paper towels, olive oil",
    )
    categories = sorted({p.category for p in products})
    chosen = f2.multiselect("Category", categories, key="inv_categories")
    only_grocery = f3.checkbox("Grocery only", key="inv_grocery_only")

    if search:
        needle = search.casefold()
        products = [p for p in products if needle in p.name.casefold()]
    if chosen:
        products = [p for p in products if p.category in chosen]
    if only_grocery:
        products = [p for p in products if p.source == "grocery"]

    frame_rows = []
    show_value = user.can(Permission.VIEW_FINANCE)
    for p in products:
        row = {"SKU": p.sku, "Product": p.name, "Category": p.category,
               "Price": f"${p.unit_price:.2f}", "Stock": p.stock, "Age (days)": p.age_days,
               "Rating": f"{p.rating:.1f} ({p.reviews})" if p.rating is not None else "—",
               "Source": p.source,
               "Overstocked": p.age_days >= 90 or p.stock >= 100}
        if show_value:
            row["Value"] = f"${p.inventory_value:,.2f}"
        frame_rows.append(row)

    st.dataframe(pd.DataFrame(frame_rows), hide_index=True, width="stretch")
    if show_value:
        total = sum(p.inventory_value for p in products)
        st.caption(
            f"{len(products)} SKUs shown · total inventory value ${total:,.2f} · "
            "overstocked = age ≥ 90 d **or** stock ≥ 100 u"
        )
    else:
        st.caption(
            f"{len(products)} SKUs shown · overstocked = age ≥ 90 d **or** stock ≥ 100 u"
        )

    if search:
        product, score = system.inventory.match(search)
        if product is None:
            st.caption(f"No catalogue match for {search!r} (best score {score:.2f}).")
        else:
            st.caption(
                f"Matcher: {product.sku} · {product.name} · score {score:.2f} "
                "(same path the customer agent uses)"
            )

    reservations = list(system.inventory.reservations.values())
    if reservations:
        st.markdown("### Active reservations")
        st.dataframe(
            pd.DataFrame([
                {"reservation": r.reservation_id, "sku": r.sku, "qty": r.quantity,
                 "customer": r.customer_id, "order": r.order_id}
                for r in reservations
            ]),
            hide_index=True, width="stretch",
        )


# --------------------------------------------------------------------------- #
# Referrals & finance
# --------------------------------------------------------------------------- #

def _render_finance(user: User, system: EcomopsSystem) -> None:
    snapshot = finance_snapshot(system)
    st.markdown("### Finance")
    st.caption(finance_summary_line(snapshot))

    cols = st.columns(5)
    cols[0].metric("Gross revenue", f"${snapshot['gross_revenue']:,.2f}")
    cols[1].metric("Refunds", f"${snapshot['refund_total']:,.2f}")
    cols[2].metric("Net revenue", f"${snapshot['net_revenue']:,.2f}")
    cols[3].metric("Markdown value", f"${snapshot['markdown_value']:,.2f}")
    cols[4].metric("Inventory value", f"${snapshot['inventory_value']:,.2f}")

    st.markdown("### Referrals")
    referrals = referral_rows()
    frame = pd.DataFrame([row.as_dict() for row in referrals])
    left, right = st.columns([2, 3])

    with left:
        st.dataframe(
            frame.rename(columns={
                "partner": "Partner", "channel": "Channel", "clicks": "Clicks",
                "orders": "Orders", "conversion_pct": "CVR %", "revenue": "Revenue",
                "commission": "Commission", "status": "Status",
            }),
            hide_index=True, width="stretch",
        )
        st.caption(
            f"{int(frame['orders'].sum()):,} referred orders · "
            f"${frame['revenue'].sum():,.2f} revenue · "
            f"${frame['commission'].sum():,.2f} commission "
            f"(${snapshot['commission_due']:,.2f} due, ${snapshot['commission_paid']:,.2f} paid)"
        )

    with right:
        if alt is not None:
            chart = (
                alt.Chart(frame)
                .mark_bar(cornerRadiusEnd=2)
                .encode(
                    x=alt.X("revenue:Q", title="revenue ($)"),
                    y=alt.Y("partner:N", sort="-x", title=None),
                    color=alt.Color(
                        "channel:N", legend=None,
                        scale=alt.Scale(
                            range=[
                                _palette().gold,
                                _palette().accent_2,
                                _palette().accent_3,
                                _palette().accent_4,
                            ]
                        ),
                    ),
                    tooltip=["partner", "channel", "orders", "revenue", "commission"],
                )
                .properties(height=220)
                .configure(background="transparent")
                .configure_axis(
                    labelColor=_palette().muted, titleColor=_palette().muted,
                    gridColor=_palette().tint_strong, domainColor=_palette().line,
                )
            )
            st.altair_chart(chart, width="stretch")
        else:  # pragma: no cover
            st.bar_chart(frame.set_index("partner")["revenue"])

        pending = snapshot["pending_approvals"]
        if pending:
            st.info(f"{pending} proposal(s) still await an approval decision.")
        if user.can(Permission.APPROVE_FINANCE):
            st.caption("Refund approvals are handled in the Approvals tab (finance scope).")

    st.caption(
        "Referral figures are a stable demo ledger derived deterministically from the "
        "partner set; revenue/refunds/markdowns are derived from recorded events."
    )


# --------------------------------------------------------------------------- #
# View dispatch — the sidebar is the primary navigation
# --------------------------------------------------------------------------- #

_VIEWS: dict[str, Any] = {
    "overview": _render_overview,
    "request": _render_request,
    "stock": _render_stock,
    "transit": _render_transit,
    "returns": _render_returns,
    "approvals": _render_approvals,
    "trace": _render_trace,
    "inventory": _render_inventory,
    "finance": _render_finance,
    "tasks": _render_tasks,
}

_view = st.session_state.get("view")
if _view not in {key for key, _label, _icon in _visible_nav(user)}:
    st.error("Your role has no assigned workspace. Ask the MD to review your access.")
    st.stop()

with st.container(key="msm_view"):
    _VIEWS[_view](user, system)
