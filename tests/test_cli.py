"""Test the CLI commands."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from eu.spotlightforbugs.guac_sync.cli import (
    AddCommand, ListCommand, TestAuthCommand, DebugVmsCommand,
    TestNetworkCommand, AutoCommand, WebCommand, InteractiveCommand,
    create_application, main
)


class TestCLICommands:
    """Test CLI commands."""
    
    @patch('eu.spotlightforbugs.guac_sync.cli.interactive_add_vm')
    def test_add_command_success(self, mock_interactive_add_vm):
        """Test successful add command execution."""
        mock_interactive_add_vm.return_value = None
        
        command = AddCommand()
        # Mock the line method
        command.line = Mock()
        command.line_error = Mock()
        
        result = command.handle()
        
        assert result == 0
        mock_interactive_add_vm.assert_called_once()
        command.line.assert_called_with("Adding new VM connection...")
    
    @patch('eu.spotlightforbugs.guac_sync.cli.interactive_add_vm')
    def test_add_command_keyboard_interrupt(self, mock_interactive_add_vm):
        """Test add command with keyboard interrupt."""
        mock_interactive_add_vm.side_effect = KeyboardInterrupt()
        
        command = AddCommand()
        command.line = Mock()
        command.line_error = Mock()
        
        result = command.handle()
        
        assert result == 1
        command.line.assert_called_with("\nOperation cancelled by user.")
    
    @patch('eu.spotlightforbugs.guac_sync.cli.interactive_add_vm')
    def test_add_command_exception(self, mock_interactive_add_vm):
        """Test add command with exception."""
        mock_interactive_add_vm.side_effect = Exception("Test error")
        
        command = AddCommand()
        command.line = Mock()
        command.line_error = Mock()
        
        result = command.handle()
        
        assert result == 1
        command.line_error.assert_called_with("Error adding VM: Test error")
    
    @patch('eu.spotlightforbugs.guac_sync.cli.list_connections')
    def test_list_command_success(self, mock_list_connections):
        """Test successful list command execution."""
        mock_list_connections.return_value = None
        
        command = ListCommand()
        command.line = Mock()
        command.line_error = Mock()
        
        result = command.handle()
        
        assert result == 0
        mock_list_connections.assert_called_once()
        command.line.assert_called_with("Listing existing connections...")
    
    @patch('eu.spotlightforbugs.guac_sync.cli.Config')
    @patch('eu.spotlightforbugs.guac_sync.cli.ProxmoxAPI')
    def test_test_auth_command_success(self, mock_proxmox_api_class, mock_config_class):
        """Test successful test-auth command execution."""
        mock_config = Mock()
        mock_config_class.return_value = mock_config
        
        mock_proxmox_api = Mock()
        mock_proxmox_api.test_auth.return_value = None
        mock_proxmox_api_class.return_value = mock_proxmox_api
        
        command = TestAuthCommand()
        command.line = Mock()
        command.line_error = Mock()
        
        result = command.handle()
        
        assert result == 0
        mock_config_class.assert_called_once()
        mock_proxmox_api_class.assert_called_once_with(mock_config)
        mock_proxmox_api.test_auth.assert_called_once()
    
    @patch('eu.spotlightforbugs.guac_sync.cli.NetworkScanner')
    def test_test_network_command_found(self, mock_network_scanner):
        """Test test-network command when MAC is found."""
        mock_network_scanner.find_mac_on_network.return_value = {
            'ip': '192.168.1.100',
            'hostname': 'test-host'
        }
        
        command = TestNetworkCommand()
        command.line = Mock()
        command.line_error = Mock()
        command.argument = Mock(return_value="aa:bb:cc:dd:ee:ff")
        
        result = command.handle()
        
        assert result == 0
        mock_network_scanner.find_mac_on_network.assert_called_once_with("aa:bb:cc:dd:ee:ff")
    
    @patch('eu.spotlightforbugs.guac_sync.cli.NetworkScanner')
    def test_test_network_command_not_found(self, mock_network_scanner):
        """Test test-network command when MAC is not found."""
        mock_network_scanner.find_mac_on_network.return_value = None
        
        command = TestNetworkCommand()
        command.line = Mock()
        command.line_error = Mock()
        command.argument = Mock(return_value="aa:bb:cc:dd:ee:ff")
        
        result = command.handle()
        
        assert result == 0
        # Should show "Not found" message
        command.line.assert_any_call("Not found")
    
    @patch('eu.spotlightforbugs.guac_sync.cli.auto_process_all_vms')
    def test_auto_command_success(self, mock_auto_process):
        """Test successful auto command execution."""
        mock_auto_process.return_value = None
        
        command = AutoCommand()
        command.line = Mock()
        command.line_error = Mock()
        command.option = Mock(return_value=False)  # force = False
        
        result = command.handle()
        
        assert result == 0
        mock_auto_process.assert_called_once_with(force=False)
    
    @patch('eu.spotlightforbugs.guac_sync.cli.auto_process_all_vms')
    def test_auto_command_force(self, mock_auto_process):
        """Test auto command with force option."""
        mock_auto_process.return_value = None
        
        command = AutoCommand()
        command.line = Mock()
        command.line_error = Mock()
        command.option = Mock(return_value=True)  # force = True
        
        result = command.handle()
        
        assert result == 0
        mock_auto_process.assert_called_once_with(force=True)
    
    @patch('subprocess.run')
    @patch('os.chdir')
    def test_web_command_success(self, mock_chdir, mock_subprocess_run):
        """Test successful web command execution."""
        mock_subprocess_run.return_value = None
        
        command = WebCommand()
        command.line = Mock()
        command.line_error = Mock()
        command.option = Mock(side_effect=lambda x: {"port": "8501", "host": "localhost"}.get(x))
        
        result = command.handle()
        
        assert result == 0
        mock_chdir.assert_called_once()
        mock_subprocess_run.assert_called_once()
    
    @patch('subprocess.run')
    @patch('os.chdir') 
    def test_web_command_keyboard_interrupt(self, mock_chdir, mock_subprocess_run):
        """Test web command with keyboard interrupt."""
        mock_subprocess_run.side_effect = KeyboardInterrupt()
        
        command = WebCommand()
        command.line = Mock()
        command.line_error = Mock()
        command.option = Mock(side_effect=lambda x: {"port": "8501", "host": "localhost"}.get(x))
        
        result = command.handle()
        
        assert result == 0
        command.line.assert_any_call("\nWeb interface stopped by user.")
    
    @patch('subprocess.run')
    @patch('os.chdir')
    def test_web_command_file_not_found(self, mock_chdir, mock_subprocess_run):
        """Test web command when streamlit is not found."""
        mock_subprocess_run.side_effect = FileNotFoundError()
        
        command = WebCommand()
        command.line = Mock()
        command.line_error = Mock()
        command.option = Mock(side_effect=lambda x: {"port": "8501", "host": "localhost"}.get(x))
        
        result = command.handle()
        
        assert result == 1
        command.line_error.assert_called_with("Streamlit not found. Install with: pip install streamlit")


class TestCLIApplication:
    """Test CLI application setup."""
    
    def test_create_application(self):
        """Test application creation."""
        app = create_application()
        
        assert app.name == "guac-sync"
        assert app.version == "1.0.0"
        
        # Check that all expected commands are registered
        command_names = [cmd.name for cmd in app.all().values()]
        expected_commands = [
            "add", "list", "test-auth", "debug-vms", 
            "test-network", "auto", "web", "interactive"
        ]
        
        for expected_cmd in expected_commands:
            assert expected_cmd in command_names
    
    @patch('eu.spotlightforbugs.guac_sync.cli.create_application')
    def test_main_entry_point(self, mock_create_app):
        """Test main entry point."""
        mock_app = Mock()
        mock_app.run.return_value = 0
        mock_create_app.return_value = mock_app
        
        result = main()
        
        assert result == 0
        mock_create_app.assert_called_once()
        mock_app.run.assert_called_once()


class TestInteractiveCommand:
    """Test interactive command."""
    
    @patch('builtins.input')
    @patch('eu.spotlightforbugs.guac_sync.cli.interactive_add_vm')
    @patch('eu.spotlightforbugs.guac_sync.cli.list_connections')
    @patch('eu.spotlightforbugs.guac_sync.cli.auto_process_all_vms')
    def test_interactive_command_menu_choices(self, mock_auto, mock_list, mock_add, mock_input):
        """Test interactive command menu choices."""
        # Simulate user choosing options 1, 2, 3, then 4 to exit
        mock_input.side_effect = ["1", "2", "3", "4"]
        
        command = InteractiveCommand()
        command.line = Mock()
        command.line_error = Mock()
        
        result = command.handle()
        
        assert result == 0
        mock_add.assert_called_once()
        mock_list.assert_called_once()
        mock_auto.assert_called_once_with(force=False)
        
        # Check that goodbye message was shown
        command.line.assert_any_call("Goodbye!")
    
    @patch('builtins.input')
    def test_interactive_command_invalid_choice(self, mock_input):
        """Test interactive command with invalid choice."""
        # Simulate user choosing invalid option then exit
        mock_input.side_effect = ["5", "4"]
        
        command = InteractiveCommand()
        command.line = Mock()
        command.line_error = Mock()
        
        result = command.handle()
        
        assert result == 0
        command.line.assert_any_call("Invalid choice. Please enter 1-4.")
    
    @patch('builtins.input')
    def test_interactive_command_keyboard_interrupt(self, mock_input):
        """Test interactive command with keyboard interrupt."""
        mock_input.side_effect = KeyboardInterrupt()
        
        command = InteractiveCommand()
        command.line = Mock()
        command.line_error = Mock()
        
        result = command.handle()
        
        assert result == 0
        command.line.assert_any_call("\nExiting...")