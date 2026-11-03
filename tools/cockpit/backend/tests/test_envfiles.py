from config import envfiles


def _files(tmp_path, local: str, defaults: str):
    (tmp_path / ".env").write_text(local)
    (tmp_path / "defaults.env").write_text(defaults)
    return tmp_path / ".env", tmp_path / "defaults.env"


def test_parse_handles_quotes_comments_and_export(tmp_path):
    path = tmp_path / "x.env"
    path.write_text(
        "# comment\nexport A=1\nB='two words'\nC=\"x # not a comment\"\nD=4  # trailing\n"
        "bad-key=5\nE=\n"
    )
    assert envfiles.parse(path) == {
        "A": "1",
        "B": "two words",
        "C": "x # not a comment",
        "D": "4",
        "E": "",
    }


def test_process_env_beats_dotenv_beats_defaults(tmp_path):
    local, defaults = _files(tmp_path, "PORT=2\nONLY_LOCAL=l\n", "PORT=3\nHOST=d\n")
    environ = {"PORT": "1"}
    applied = envfiles.load(local, defaults, environ)
    assert environ == {"PORT": "1", "ONLY_LOCAL": "l", "HOST": "d"}
    assert applied == {"ONLY_LOCAL": "l", "HOST": "d"}


def test_dotenv_beats_defaults(tmp_path):
    local, defaults = _files(tmp_path, "PORT=2\n", "PORT=3\n")
    environ: dict[str, str] = {}
    envfiles.load(local, defaults, environ)
    assert environ["PORT"] == "2"


def test_test_stage_keeps_only_connection_settings_from_dotenv(tmp_path):
    local, defaults = _files(
        tmp_path, "COCKPIT_DB_PASSWORD=pw\nREDIS_URL=redis://x\nCOCKPIT_FAKE=1\n", ""
    )
    environ = {"COCKPIT_ENV": "test"}
    envfiles.load(local, defaults, environ)
    assert environ["COCKPIT_DB_PASSWORD"] == "pw"
    assert environ["REDIS_URL"] == "redis://x"
    assert "COCKPIT_FAKE" not in environ


def test_defaults_file_documents_every_key():
    """Every key in defaults.env sits in a block (lines between blank lines) that starts
    with a comment (§13)."""
    commented = False
    for line in envfiles.DEFAULTS.read_text().splitlines():
        if not line.strip():
            commented = False
        elif line.startswith("#"):
            commented = True
        else:
            assert commented, f"undocumented: {line}"


def test_paths():
    assert (envfiles.REPO / "cockpit.sh").exists()
    assert envfiles.DEFAULTS.name == "defaults.env" and envfiles.LOCAL == envfiles.REPO / ".env"


def test_flags(monkeypatch):
    for value, expected in (("1", True), ("TRUE", True), (" on ", True), ("yes", True),
                            ("0", False), ("no", False), ("", False)):  # fmt: skip
        monkeypatch.setenv("X_FLAG", value)
        assert envfiles.flag("X_FLAG") is expected
    monkeypatch.delenv("X_FLAG")
    assert envfiles.flag("X_FLAG", "1") is True


def test_source_of(tmp_path):
    local, defaults = _files(tmp_path, "BOTH=l\nONLY_LOCAL=l\n", "BOTH=d\nONLY_DEFAULT=d\n")
    environ = {"FROM_ENV": "e"}
    assert envfiles.source_of("FROM_ENV", local, defaults, environ) == "env"
    assert envfiles.source_of("ONLY_LOCAL", local, defaults, environ) == ".env"
    assert envfiles.source_of("BOTH", local, defaults, environ) == ".env"
    assert envfiles.source_of("ONLY_DEFAULT", local, defaults, environ) == "defaults.env"
    assert envfiles.source_of("NOWHERE", local, defaults, environ) == "unset"
    assert environ == {"FROM_ENV": "e"}  # it only reads


def test_unclosed_quotes_and_invalid_keys(tmp_path):
    path = tmp_path / "x.env"
    path.write_text("A='open\n1B=x\nC D=y\nexport\tE=tab\nF=a#b\n")
    assert envfiles.parse(path) == {"A": "open", "E": "tab", "F": "a#b"}
