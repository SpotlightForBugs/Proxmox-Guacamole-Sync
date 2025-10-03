# Security Policy

## Overview

Proxmox-Guacamole Sync is a bridge tool that handles sensitive credentials and network connections between Proxmox VE and Apache Guacamole. This document outlines our security practices and how to report vulnerabilities.

## Supported Versions

We provide security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| < Latest| :x:                |

Since this is a single-file Python tool, we recommend always using the latest version from the main branch.

## Security Features

### Password Encryption
- **Fernet Encryption**: All passwords can be encrypted using cryptography.fernet before storage in Proxmox VM notes
- **Key Management**: Encryption keys are configured in `config.py` (git-ignored)
- **Automatic Migration**: Plain-text passwords are automatically detected and encrypted when possible

### API Security
- **Token-based Authentication**: Uses Proxmox API tokens instead of username/password
- **SSL/TLS Support**: Supports both valid and self-signed certificates
- **Session Management**: Proper session handling for both Proxmox and Guacamole APIs

### Network Security
- **Local Network Scanning**: Network discovery is limited to local subnet scanning
- **No External Dependencies**: Wake-on-LAN implementation avoids external libraries
- **IPv4 Only**: Explicitly IPv4-only to prevent IPv6 leakage

### Credential Handling
- **Structured Format**: Credentials are parsed from VM notes in a structured format
- **Template Variables**: Support for placeholder replacement without exposing sensitive data
- **Separation of Concerns**: Config credentials separate from VM-specific credentials

## Security Considerations

### Before Deployment

1. **Review Configuration**
   - Ensure `config.py` contains proper API credentials
   - Set up encryption key for password protection
   - Verify SSL certificate settings match your environment

2. **Network Security**
   - Deploy on a trusted network segment with access to both Proxmox and Guacamole
   - Ensure proper firewall rules for API access
   - Consider network segmentation for management traffic

3. **Access Control**
   - Limit file system access to the tool's directory
   - Use dedicated service accounts with minimal required permissions
   - Regularly rotate API tokens and encryption keys

### VM Notes Security

- **Sensitive Data**: VM notes may contain encrypted passwords and connection details
- **Access Control**: Ensure Proxmox users have appropriate permissions for VM note access
- **Backup Considerations**: Be aware that VM backups may include credential data

### API Permissions

#### Proxmox Requirements
Minimum required Proxmox permissions:
- `VM.Config.Desc` (read/write VM descriptions/notes)
- `VM.Monitor` (read VM status and network info)
- `VM.PowerMgmt` (start/stop VMs for IP detection)
- `Sys.Audit` (read node and VM information)

#### Guacamole Requirements
- Administrative access to create/modify connections and groups
- Database access (MySQL/PostgreSQL) through REST API

## Vulnerability Reporting

### How to Report

If you discover a security vulnerability, please report it responsibly:

1. **Email**: Send details to the repository owner via GitHub private message
2. **GitHub Security Advisories**: Use GitHub's private vulnerability reporting feature
3. **Do NOT**: Open public issues for security vulnerabilities

### What to Include

Please provide the following information:

- **Description**: Clear description of the vulnerability
- **Impact**: Potential impact and attack scenarios
- **Reproduction**: Steps to reproduce the issue
- **Environment**: Python version, OS, and relevant configuration details
- **Suggested Fix**: If you have recommendations for fixing the issue

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial Assessment**: Within 5 business days
- **Status Updates**: Weekly until resolved
- **Fix Timeline**: Depends on severity (see below)

## Severity Classification

### Critical (Fix within 1-3 days)
- Remote code execution
- Authentication bypass
- Credential exposure in logs/output

### High (Fix within 1-2 weeks)
- Local privilege escalation
- Significant data exposure
- Network-based attacks

### Medium (Fix within 4 weeks)
- Information disclosure
- Denial of service
- Configuration weaknesses

### Low (Fix when possible)
- Minor information leaks
- Non-security bugs with security implications

## Security Best Practices

### For Users

1. **Keep Updated**: Always use the latest version
2. **Secure Config**: Keep `config.py` with appropriate file permissions (600)
3. **Monitor Access**: Regularly audit who has access to Proxmox and Guacamole
4. **Log Review**: Monitor both system and application logs for unusual activity
5. **Network Monitoring**: Watch for unexpected network traffic from the tool

### For Developers

1. **Input Validation**: All user inputs and API responses are validated
2. **Error Handling**: Sensitive information is not exposed in error messages
3. **Logging**: Avoid logging sensitive data (passwords, tokens)
4. **Dependencies**: Keep Python dependencies updated and minimal
5. **Code Review**: All changes should be reviewed for security implications

## Known Security Limitations

1. **Self-signed Certificates**: SSL warnings are disabled by default for self-signed certificates
   - **Intentional Design**: This tool is designed for internal infrastructure where self-signed certificates are common
   - **Security Scanner Note**: Static analysis tools will flag this as a security issue, but it's expected behavior
   - **Mitigation**: Ensure the tool runs only on trusted networks with known Proxmox/Guacamole endpoints

2. **Network Scanning**: Tool performs local network discovery which may be detected by security tools
   - ARP table scanning and ping sweeps are required for VM IP detection
   - These operations are intentional and necessary for the tool's functionality

3. **URL Construction from Config**: The tool constructs API URLs from user-provided configuration
   - **Security Scanner Note**: May be flagged as potential SSRF (Server-Side Request Forgery)
   - **Acceptable Risk**: This is intentional - the tool MUST connect to user-specified Proxmox/Guacamole servers
   - **Mitigation**: 
     - Configuration file (`config.py`) is git-ignored and user-controlled
     - Tool is designed for trusted internal networks only
     - Not exposed to untrusted user input or public networks

4. **Credential Storage**: VM notes are the primary credential storage mechanism
   - Passwords are encrypted using Fernet encryption
   - VM notes are accessible to Proxmox administrators

5. **Single-file Design**: All functionality in one file limits code isolation
   - Trade-off for simplicity and ease of deployment

## Security Scanner Warnings

This tool is designed for **internal infrastructure management** and will trigger security scanner warnings that are **acceptable** for its use case:

### Expected Warnings

1. **SSL Certificate Verification Disabled (B501)**
   - **Why**: Self-signed certificates are standard in internal infrastructure
   - **Acceptable**: Tool operates on trusted internal networks with known endpoints

2. **Potential SSRF in Request Functions**
   - **Why**: Tool constructs URLs from config file to connect to user's infrastructure
   - **Acceptable**: All URL inputs are from trusted sources (config.py, Proxmox API responses)
   - **Not Applicable**: No untrusted user input flows into requests

3. **Network Operations (ARP/Ping)**
   - **Why**: Required for VM IP discovery when guest agent unavailable
   - **Acceptable**: Essential functionality for the tool's purpose

### When Warnings Are NOT Acceptable

Security scanner warnings **should be investigated** if:
- User input from CLI directly flows into URLs without validation
- Passwords are logged or printed in plain text
- New network operations are added without proper scoping
- External/untrusted data sources are introduced

## Compliance Considerations

This tool handles:
- **Network credentials** (usernames/passwords)
- **Infrastructure access** (VM management)
- **Remote desktop sessions** (connection brokering)

Organizations should evaluate compliance requirements for:
- Data protection regulations (GDPR, CCPA, etc.)
- Infrastructure security standards (SOC 2, ISO 27001, etc.)
- Industry-specific requirements (PCI-DSS, HIPAA, etc.)

## Security Testing

### Recommended Tests

1. **Credential Handling**
   - Verify passwords are properly encrypted
   - Test credential parsing edge cases
   - Validate template variable substitution

2. **API Security**
   - Test authentication failure handling
   - Verify SSL certificate validation behavior
   - Test API rate limiting and timeouts

3. **Network Security**
   - Validate MAC address scanning scope
   - Test Wake-on-LAN packet generation
   - Verify IPv4-only behavior

### Automated Security

Consider integrating:
- **Static Analysis**: Use tools like `bandit` for Python security analysis
- **Dependency Scanning**: Monitor for vulnerable dependencies
- **Secret Detection**: Scan for accidentally committed credentials

## Contact Information

For security-related questions or concerns:
- **Repository**: [GitHub Issues](https://github.com/SpotlightForBugs/Proxmox-Guacamole-Sync/issues) (for non-sensitive questions)
- **Security Reports**: Use GitHub's private vulnerability reporting
- **General Contact**: Through GitHub repository maintainer

---

*This security policy is reviewed and updated regularly. Last updated: September 29, 2025*