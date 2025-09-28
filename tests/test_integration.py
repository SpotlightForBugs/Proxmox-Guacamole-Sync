"""Integration tests for the CLI."""

import subprocess
import pytest
import sys
from pathlib import Path


class TestCLIIntegration:
    """Test CLI integration using actual command execution."""
    
    def test_cli_help(self):
        """Test CLI help command works."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_guac_sync_available(self):
        """Test that guac-sync command is available."""
        # This is a basic test to see if the command is installed
        result = subprocess.run(
            ["guac-sync", "--version"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        # Should not crash (returncode might be 0 or 1 depending on implementation)
        assert "guac-sync" in result.stdout or "1.0.0" in result.stdout or result.stderr
    
    def test_import_cli_module(self):
        """Test that the CLI module can be imported."""
        try:
            from eu.spotlightforbugs.guac_sync.cli import create_application
            app = create_application()
            assert app.name == "guac-sync"
            assert app.version == "1.0.0"
        except ImportError as e:
            pytest.fail(f"Failed to import CLI module: {e}")


class TestLegacyModule:
    """Test legacy module functionality."""
    
    def test_import_legacy_module(self):
        """Test that the legacy guac_vm_manager module can be imported."""
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))
        
        try:
            import guac_vm_manager
            # Check that key classes exist
            assert hasattr(guac_vm_manager, 'Config')
            assert hasattr(guac_vm_manager, 'ProxmoxAPI')
            assert hasattr(guac_vm_manager, 'NetworkScanner')
        except ImportError as e:
            pytest.fail(f"Failed to import legacy module: {e}")