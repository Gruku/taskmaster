# User intent: the shared Caddyfile reader used by the deploy tests is itself pinned, so a parser slip can't make a bad Caddyfile look right.
from pathlib import Path

from caddyfile import directive, matcher, sites, subdirective, upstreams

CADDYFILE = Path(__file__).resolve().parents[1] / "deploy" / "caddy" / "Caddyfile"

SAMPLE = """
{
	admin off
}
# commented.example.com {
# 	respond 404
# }
a.example.com {
	log {
		output file /x {
			roll_keep 14
		}
		format filter {
			request>headers>X delete
		}
	}
	@a path /a /a/*
	handle @a {
		reverse_proxy 127.0.0.1:1 {
			transport http {
				keepalive off
			}
		}
	}
	handle {
		respond 404
	}
}
b.example.com {
	header Strict-Transport-Security "max-age=31536000"
}
"""


def test_sites_on_the_shipped_file_returns_both_names_in_order():
    assert list(sites(CADDYFILE.read_text(encoding="utf-8"))) == ["tm.example.com", "viewer.tm.example.com"]


def test_a_commented_block_and_the_global_block_are_ignored():
    assert list(sites(SAMPLE)) == ["a.example.com", "b.example.com"]


def test_nested_braces_do_not_confuse_depth():
    body = sites(SAMPLE)["a.example.com"]
    assert upstreams(body) == ["127.0.0.1:1"]
    assert directive(body, "respond") == ["404"]
    assert directive(sites(SAMPLE)["b.example.com"], "header") == ['Strict-Transport-Security "max-age=31536000"']


def test_matcher_reads_each_sites_own_matcher():
    shipped = sites(CADDYFILE.read_text(encoding="utf-8"))
    assert "/mcp/*" in matcher(shipped["tm.example.com"], "taskmaster")
    assert "/api/*" in matcher(shipped["viewer.tm.example.com"], "viewer")
    assert matcher(sites(SAMPLE)["a.example.com"], "a") == ["/a", "/a/*"]


def test_subdirective_reads_a_nested_path():
    body = sites(SAMPLE)["a.example.com"]
    assert subdirective(body, "reverse_proxy/transport/keepalive") == ["off"]
    assert subdirective(body, "reverse_proxy/transport/max_conns_per_host") == []
    assert subdirective(body, "log/format/request>headers>X") == ["delete"]
