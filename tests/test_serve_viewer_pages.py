# User intent: the pages the public viewer serves (app, sign-in, markdown file page) carry no inline script, no <style> and no style attribute, so a strict CSP can hold, and the markdown page loads only the plugin's own files.
from html.parser import HTMLParser

import pytest

from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_viewer import login, make_viewer, req, v, work_tree  # noqa: F401


class Walk(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline_scripts, self.styles, self.style_attrs, self.script_srcs, self.links = [], 0, [], [], []
        self.elements = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        self.elements.append((tag, d))
        if "style" in d:
            self.style_attrs.append(tag)
        if tag == "style":
            self.styles += 1
        if tag == "script":
            if "src" in d:
                self.script_srcs.append(d["src"])
            else:
                self._in_script = True
        if tag == "link" and d.get("rel") == "stylesheet":
            self.links.append(d.get("href"))

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.inline_scripts.append(data.strip()[:40])

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False


def walk(text: str) -> Walk:
    w = Walk()
    w.feed(text)
    return w


def assert_no_inline_code(text: str):
    w = walk(text)
    assert w.inline_scripts == [] and w.styles == 0 and w.style_attrs == [], (w.inline_scripts, w.styles, w.style_attrs)
    return w


def test_the_app_and_sign_in_pages_have_no_inline_code(v):
    cookie, _ = login(v)
    app = assert_no_inline_code(req(v, "GET", "/", headers={"Cookie": cookie}).text)
    assert "/static/v3/js/theme-boot.js" in app.script_srcs
    assert app.script_srcs.index("/static/v3/js/theme-boot.js") < app.script_srcs.index("/static/v3/vendor/marked.min.js")
    sign_in = assert_no_inline_code(req(v, "GET", "/login").text)
    assert "/static/v3/css/login.css" in sign_in.links
    error = assert_no_inline_code(req(v, "POST", "/login", headers={"Origin": v.loopback}, body="token=x").text)
    assert error.links


@pytest.mark.parametrize("path", ["css/login.css", "js/theme-boot.js"])
def test_the_new_assets_are_served_to_anyone(v, path):
    assert req(v, "GET", "/static/v3/" + path).status == 200


# --- the /file/ markdown page (Step 7) -----------------------------------------------------------

import base64
import re

from taskmaster import backlog_server as bs

SCRIPTS = ["/static/v3/vendor/marked.min.js", "/static/v3/js/lib/md-safe.js", "/static/v3/js/file-page.js"]
STYLES = ["/static/v3/css/tokens.css", "/static/v3/css/components/markdown.css", "/static/v3/css/file-page.css"]


def assert_md_page(text: str, source: str):
    assert "jsdelivr" not in text and "https://" not in text and "http://" not in text
    w = assert_no_inline_code(text)
    assert w.script_srcs == SCRIPTS and w.links == STYLES
    encoded = re.search(r'data-b64="([^"]*)"', text).group(1)
    assert base64.b64decode(encoded).decode("utf-8") == source
    return w


def test_the_guarded_markdown_page_loads_only_the_plugins_files_and_has_no_editor_link(v, work_tree):
    cookie, _ = login(v)
    text = req(v, "GET", "/file/docs/a.md", headers={"Cookie": cookie}).text
    assert_md_page(text, "# A\n")
    assert "vscode://" not in text and "open-editor" not in text
    assert str(work_tree) not in text and work_tree.as_posix() not in text
    assert 'id="content" class="md-body"' in text


def test_the_solo_markdown_page_is_the_same_page_with_its_editor_link(tmp_path):
    handler = bs.ViewerHandler.__new__(bs.ViewerHandler)
    resolved = tmp_path / "docs" / "a.md"
    link = handler._md_editor_link(resolved)
    assert link.startswith('<a href="vscode://file/') and 'class="open-editor"' in link
    assert "&lt;" not in link


def test_the_editor_link_escapes_the_path():
    handler = bs.ViewerHandler.__new__(bs.ViewerHandler)
    handler._md_full_path = lambda resolved: '/r/a"><img src=x onerror=alert(1)>.md'
    link = handler._md_editor_link(None)
    w = walk(link)
    assert [t for t, _ in w.elements] == ["a"]                          # no <img> element was created
    assert w.elements[0][1]["href"] == 'vscode://file//r/a"><img src=x onerror=alert(1)>.md'


def test_a_file_name_with_a_placeholder_is_not_substituted_twice(v, work_tree):
    name = "{{B64CONTENT}}{{EDITOR_LINK}}.md"
    (work_tree / "docs" / name).write_text("# T\n", encoding="utf-8")
    from test_serve_viewer import git
    git(work_tree, "add", "docs")
    git(work_tree, "commit", "-q", "-m", "p")
    cookie, _ = login(v)
    v.clock.now += 31                                                    # the tracked-file list is cached for 30 s
    text = req(v, "GET", "/file/docs/" + name.replace("{", "%7B").replace("}", "%7D"), headers={"Cookie": cookie}).text
    assert text.count("data-b64=") == 1 and "open-editor" not in text
