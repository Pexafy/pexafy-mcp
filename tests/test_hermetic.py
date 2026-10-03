"""The suite cannot reach a real API, whatever the environment it runs in.

conftest.py forces the configuration and guards the network; these pin both, so a
change there that reopens the door fails here rather than going unnoticed.
"""
from __future__ import annotations

import os
import socket

import dotenv
import pytest


def test_the_server_under_test_points_at_no_real_api():
    from pexafy_mcp import server

    assert server.API_BASE_URL == "http://pexafy-api.invalid"
    assert server.client.base_url.host == "pexafy-api.invalid"
    assert server.API_KEY == ""


def test_no_dotenv_file_is_read(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PEXAFY_API_KEY=pexafy_api_from_a_file\n")
    assert dotenv.load_dotenv(env_file) is False
    assert "PEXAFY_API_KEY" not in os.environ


def test_a_real_connection_is_refused_and_reported(no_network):
    with pytest.raises(ConnectionRefusedError, match="network access blocked"):
        socket.create_connection(("127.0.0.1", 8000), timeout=1)
    with pytest.raises(socket.gaierror, match="network access blocked"):
        socket.getaddrinfo("api.pexafy.com", 443)
    assert no_network == ["connect ('127.0.0.1', 8000)", "DNS 'api.pexafy.com'"]
    no_network.clear()  # made on purpose; any other test making them fails
