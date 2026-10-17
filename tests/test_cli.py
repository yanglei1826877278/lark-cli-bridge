import os
import shutil
from pathlib import Path

import pytest

from bridge.cli import CliRunner, CommandError
from bridge.settings import Settings


def runner_for(tmp_path, launcher):
    return CliRunner(Settings(
        url='http://127.0.0.1:8765', host='127.0.0.1', port=8765,
        password='test', mock=False, cli_path=str(launcher),
        bases=frozenset(), docs=frozenset(), db_path=tmp_path/'test.sqlite3',
        timeout=10, cli_workdir=tmp_path,
    ))


def test_npm_launcher_with_minimal_path(tmp_path, monkeypatch):
    real_node = shutil.which('node')
    if not real_node:
        pytest.skip('Node is required for the subprocess regression test')
    bin_dir = tmp_path/'bin'
    bin_dir.mkdir()
    node = bin_dir/('node.exe' if os.name == 'nt' else 'node')
    shutil.copy2(real_node, node)
    script = tmp_path/'package'/'run.js'
    script.parent.mkdir()
    script.write_text('#!/usr/bin/env node\nconsole.log(JSON.stringify({ok:true,args:process.argv.slice(2)}));\n')
    launcher = bin_dir/'lark-cli'
    try:
        launcher.symlink_to(script)
    except OSError:
        pytest.skip('Creating symlinks requires permission on this platform')
    monkeypatch.setenv('PATH', '')
    runner = runner_for(tmp_path, launcher)
    assert runner.binary() == [str(node), str(script)]
    assert runner.run(['--help']) == {'ok': True, 'args': ['--help']}


def test_node_shebang_without_js_extension(tmp_path, monkeypatch):
    launcher = tmp_path/'lark-cli'
    launcher.write_text('#!/usr/bin/env node\n')
    monkeypatch.setattr(shutil, 'which', lambda name: '/available/node')
    assert runner_for(tmp_path, launcher).binary() == ['/available/node', str(launcher)]


def test_js_launcher_without_node_reports_actionable_error(tmp_path, monkeypatch):
    launcher = tmp_path/'run.js'
    launcher.write_text('#!/usr/bin/env node\n')
    monkeypatch.setenv('PATH', '')
    with pytest.raises(CommandError, match='未找到 Node'):
        runner_for(tmp_path, launcher).binary()


def test_native_executable_is_not_wrapped(tmp_path):
    launcher = tmp_path/'lark-cli.exe'
    launcher.write_bytes(b'\x7fELF\x00native executable')
    assert runner_for(tmp_path, launcher).binary() == [str(launcher)]
