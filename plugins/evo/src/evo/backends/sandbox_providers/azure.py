"""Azure VM sandbox provider.

Creates an Azure Linux VM plus minimal network resources, then reuses the
existing SSH bootstrap path to install sandbox-agent and open the local tunnel.
"""
from __future__ import annotations

import secrets
import socket
import subprocess
import time
from typing import Any

import requests
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import (
    HardwareProfile,
    ImageReference,
    LinuxConfiguration,
    NetworkInterfaceReference,
    NetworkProfile,
    OSProfile,
    SshConfiguration,
    SshPublicKey,
    StorageProfile,
    VirtualMachine,
)
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.network.models import (
    AddressSpace,
    NetworkInterface,
    NetworkInterfaceIPConfiguration,
    NetworkSecurityGroup,
    PublicIPAddress,
    SecurityRule,
    Subnet,
    VirtualNetwork,
)
from azure.mgmt.resource import ResourceManagementClient

from ..protocol import RemoteBackendUnavailable, SandboxHandle, SandboxSpec
from ._common import SandboxAgentProviderMixin
from .ssh import SSHProvider


DEFAULT_LOCATION = "westus2"
DEFAULT_VM_SIZE = "Standard_D2s_v3"
DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_HEALTH_TIMEOUT = 120.0
DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USER = "azureuser"
DEFAULT_IMAGE = "Canonical:0001-com-ubuntu-server-jammy:22_04-lts:latest"
DEFAULT_VNET_CIDR = "10.42.0.0/16"
DEFAULT_SUBNET_CIDR = "10.42.0.0/24"


class AzureProvider(SandboxAgentProviderMixin):
    name = "azure"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.subscription_id = str(
            config.get("subscription_id", "")
        ).strip()
        self.resource_group = str(
            config.get("resource_group", "")
        ).strip()
        self.location = str(
            config.get("location", DEFAULT_LOCATION)
        ).strip() or DEFAULT_LOCATION
        self.vm_size = str(
            config.get("vm_size", DEFAULT_VM_SIZE)
        ).strip() or DEFAULT_VM_SIZE
        self.image = str(
            config.get("image", DEFAULT_IMAGE)
        ).strip() or DEFAULT_IMAGE
        self.key = str(config.get("key", "")).strip() or None
        self.ssh_public_key = str(
            config.get("ssh_public_key", "")
        ).strip() or None
        self.ssh_user = str(
            config.get("ssh_user", DEFAULT_SSH_USER)
        ).strip() or DEFAULT_SSH_USER
        self.ssh_port = int(config.get("ssh_port", DEFAULT_SSH_PORT))
        self.timeout = int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.health_timeout = float(
            config.get("health_timeout_seconds", DEFAULT_HEALTH_TIMEOUT)
        )
        self.keep_warm = _parse_bool(config.get("keep_warm", False))
        self.ssh_cidr = str(config.get("ssh_cidr", "")).strip() or None
        self.vnet_cidr = str(config.get("vnet_cidr", DEFAULT_VNET_CIDR)).strip() or DEFAULT_VNET_CIDR
        self.subnet_cidr = str(
            config.get("subnet_cidr", DEFAULT_SUBNET_CIDR)
        ).strip() or DEFAULT_SUBNET_CIDR

        if not self.subscription_id:
            raise RemoteBackendUnavailable(
                "azure provider requires subscription_id "
                "(set via --provider-config subscription_id=...)."
            )
        if not self.resource_group:
            raise RemoteBackendUnavailable(
                "azure provider requires resource_group "
                "(set via --provider-config resource_group=...)."
            )
        if not self.key and not self.ssh_public_key:
            raise RemoteBackendUnavailable(
                "azure provider requires either key=<private-key-path> "
                "or ssh_public_key=<openssh-public-key>."
            )

        self._credential = DefaultAzureCredential(
            exclude_interactive_browser_credential=True
        )
        self._resource = ResourceManagementClient(
            self._credential, self.subscription_id
        )
        self._network = NetworkManagementClient(
            self._credential, self.subscription_id
        )
        self._compute = ComputeManagementClient(
            self._credential, self.subscription_id
        )

    def provision(self, spec: SandboxSpec) -> SandboxHandle:
        native_id = f"azure-{secrets.token_hex(6)}"
        prefix = f"evo-az-{native_id[-12:]}"
        vm_name = prefix
        pip_name = f"{prefix}-pip"
        nsg_name = f"{prefix}-nsg"
        vnet_name = f"{prefix}-vnet"
        subnet_name = f"{prefix}-subnet"
        nic_name = f"{prefix}-nic"

        ssh_cidr = self.ssh_cidr or _detect_public_cidr()
        ssh_public_key = self.ssh_public_key or _public_key_from_private_key(self.key)
        image_ref = _parse_image(self.image)

        self._ensure_provider_registered("Microsoft.Network")
        self._ensure_provider_registered("Microsoft.Compute")
        self._resource.resource_groups.create_or_update(
            self.resource_group, {"location": self.location}
        )

        try:
            nsg = self._network.network_security_groups.begin_create_or_update(
                self.resource_group,
                nsg_name,
                NetworkSecurityGroup(
                    location=self.location,
                    security_rules=[
                        SecurityRule(
                            name="AllowSSH",
                            protocol="Tcp",
                            source_port_range="*",
                            destination_port_range="22",
                            source_address_prefix=ssh_cidr,
                            destination_address_prefix="*",
                            access="Allow",
                            priority=1000,
                            direction="Inbound",
                        )
                    ],
                ),
            ).result()
            vnet = self._network.virtual_networks.begin_create_or_update(
                self.resource_group,
                vnet_name,
                VirtualNetwork(
                    location=self.location,
                    address_space=AddressSpace(address_prefixes=[self.vnet_cidr]),
                ),
            ).result()
            subnet = self._network.subnets.begin_create_or_update(
                self.resource_group,
                vnet.name,
                subnet_name,
                Subnet(address_prefix=self.subnet_cidr),
            ).result()
            public_ip = self._network.public_ip_addresses.begin_create_or_update(
                self.resource_group,
                pip_name,
                PublicIPAddress(
                    location=self.location,
                    sku={"name": "Standard"},
                    public_ip_allocation_method="Static",
                    public_ip_address_version="IPV4",
                ),
            ).result()
            nic = self._network.network_interfaces.begin_create_or_update(
                self.resource_group,
                nic_name,
                NetworkInterface(
                    location=self.location,
                    network_security_group={"id": nsg.id},
                    ip_configurations=[
                        NetworkInterfaceIPConfiguration(
                            name=f"{prefix}-ipcfg",
                            subnet={"id": subnet.id},
                            public_ip_address={"id": public_ip.id},
                        )
                    ],
                ),
            ).result()

            vm = self._compute.virtual_machines.begin_create_or_update(
                self.resource_group,
                vm_name,
                VirtualMachine(
                    location=self.location,
                    hardware_profile=HardwareProfile(vm_size=self.vm_size),
                    storage_profile=StorageProfile(
                        image_reference=ImageReference(**image_ref),
                    ),
                    os_profile=OSProfile(
                        computer_name=vm_name,
                        admin_username=self.ssh_user,
                        linux_configuration=LinuxConfiguration(
                            disable_password_authentication=True,
                            ssh=SshConfiguration(
                                public_keys=[
                                    SshPublicKey(
                                        path=f"/home/{self.ssh_user}/.ssh/authorized_keys",
                                        key_data=ssh_public_key,
                                    )
                                ]
                            ),
                        ),
                    ),
                    network_profile=NetworkProfile(
                        network_interfaces=[
                            NetworkInterfaceReference(
                                id=nic.id,
                                primary=True,
                            )
                        ]
                    ),
                ),
            ).result()
        except Exception as exc:
            self._cleanup_resources(
                vm_name=vm_name,
                nic_name=nic_name,
                pip_name=pip_name,
                nsg_name=nsg_name,
                vnet_name=vnet_name,
            )
            raise RemoteBackendUnavailable(
                f"Azure VM provisioning failed: {exc}"
            ) from exc

        public_ip_address = self._wait_for_public_ip(pip_name)
        ssh_provider = SSHProvider(
            {
                "host": f"{self.ssh_user}@{public_ip_address}",
                "key": self.key,
                "port": self.ssh_port,
                "keep_warm": self.keep_warm,
                "health_timeout_seconds": self.health_timeout,
            }
        )
        try:
            handle = ssh_provider.provision(spec)
        except Exception:
            self._cleanup_resources(
                vm_name=vm_name,
                nic_name=nic_name,
                pip_name=pip_name,
                nsg_name=nsg_name,
                vnet_name=vnet_name,
                os_disk_name=getattr(getattr(vm, "storage_profile", None), "os_disk", None)
                and vm.storage_profile.os_disk.name,
            )
            raise

        handle.metadata = dict(handle.metadata or {})
        handle.metadata.update(
            {
                "azure_vm_name": vm_name,
                "azure_resource_group": self.resource_group,
                "azure_subscription_id": self.subscription_id,
                "azure_location": self.location,
                "azure_public_ip_name": pip_name,
                "azure_nsg_name": nsg_name,
                "azure_vnet_name": vnet_name,
                "azure_subnet_name": subnet_name,
                "azure_nic_name": nic_name,
                "azure_public_ip": public_ip_address,
                "azure_vm_size": self.vm_size,
                "azure_image": self.image,
                "azure_ssh_user": self.ssh_user,
                "azure_ssh_port": self.ssh_port,
                "azure_keep_warm": self.keep_warm,
                "azure_os_disk_name": getattr(
                    getattr(vm, "storage_profile", None), "os_disk", None
                )
                and vm.storage_profile.os_disk.name,
            }
        )
        return handle

    def tear_down(self, handle: SandboxHandle) -> None:
        ssh_provider = self._ssh_provider_for_handle(handle)
        try:
            ssh_provider.tear_down(handle)
        finally:
            if not _parse_bool(
                (handle.metadata or {}).get("azure_keep_warm", self.keep_warm)
            ):
                meta = handle.metadata or {}
                self._cleanup_resources(
                    vm_name=meta.get("azure_vm_name"),
                    nic_name=meta.get("azure_nic_name"),
                    pip_name=meta.get("azure_public_ip_name"),
                    nsg_name=meta.get("azure_nsg_name"),
                    vnet_name=meta.get("azure_vnet_name"),
                    os_disk_name=meta.get("azure_os_disk_name"),
                )

    def is_alive(self, handle: SandboxHandle) -> bool:
        meta = handle.metadata or {}
        vm_name = meta.get("azure_vm_name")
        if not vm_name:
            return False
        try:
            vm = self._compute.virtual_machines.get(
                self.resource_group,
                vm_name,
                expand="instanceView",
            )
            statuses = list(getattr(getattr(vm, "instance_view", None), "statuses", []) or [])
            codes = {
                str(getattr(status, "code", "")).strip().lower()
                for status in statuses
            }
            if not any(code == "powerstate/running" for code in codes):
                return False
        except Exception:
            return False
        return self._ssh_provider_for_handle(handle).is_alive(handle)

    def _ssh_provider_for_handle(self, handle: SandboxHandle) -> SSHProvider:
        meta = handle.metadata or {}
        host = meta.get("azure_public_ip")
        if not host:
            raise RemoteBackendUnavailable("Azure handle missing public IP")
        return SSHProvider(
            {
                "host": f"{meta.get('azure_ssh_user', self.ssh_user)}@{host}",
                "key": self.key,
                "port": meta.get("azure_ssh_port", self.ssh_port),
                "keep_warm": _parse_bool(
                    meta.get("azure_keep_warm", self.keep_warm)
                ),
                "health_timeout_seconds": self.health_timeout,
            }
        )

    def _wait_for_public_ip(self, pip_name: str) -> str:
        deadline = time.monotonic() + self.health_timeout
        while time.monotonic() < deadline:
            pip = self._network.public_ip_addresses.get(
                self.resource_group, pip_name
            )
            ip_address = str(getattr(pip, "ip_address", "") or "").strip()
            if ip_address:
                return ip_address
            time.sleep(2.0)
        raise RemoteBackendUnavailable(
            f"Azure public IP {pip_name!r} did not become ready within "
            f"{self.health_timeout}s."
        )

    def _cleanup_resources(
        self,
        *,
        vm_name: str | None = None,
        nic_name: str | None = None,
        pip_name: str | None = None,
        nsg_name: str | None = None,
        vnet_name: str | None = None,
        os_disk_name: str | None = None,
    ) -> None:
        if vm_name:
            try:
                self._compute.virtual_machines.begin_delete(
                    self.resource_group, vm_name
                ).result()
            except Exception:
                pass
        if os_disk_name:
            try:
                self._compute.disks.begin_delete(
                    self.resource_group, os_disk_name
                ).result()
            except Exception:
                pass
        if nic_name:
            try:
                self._network.network_interfaces.begin_delete(
                    self.resource_group, nic_name
                ).result()
            except Exception:
                pass
        if pip_name:
            try:
                self._network.public_ip_addresses.begin_delete(
                    self.resource_group, pip_name
                ).result()
            except Exception:
                pass
        if nsg_name:
            try:
                self._network.network_security_groups.begin_delete(
                    self.resource_group, nsg_name
                ).result()
            except Exception:
                pass
        if vnet_name:
            try:
                self._network.virtual_networks.begin_delete(
                    self.resource_group, vnet_name
                ).result()
            except Exception:
                pass

    def _ensure_provider_registered(self, namespace: str) -> None:
        try:
            provider = self._resource.providers.get(namespace)
            state = str(getattr(provider, "registration_state", "")).strip().lower()
            if state == "registered":
                return
            self._resource.providers.register(namespace)
            deadline = time.monotonic() + 180.0
            while time.monotonic() < deadline:
                provider = self._resource.providers.get(namespace)
                state = str(getattr(provider, "registration_state", "")).strip().lower()
                if state == "registered":
                    return
                time.sleep(2.0)
        except Exception as exc:
            raise RemoteBackendUnavailable(
                f"Azure subscription is not ready for {namespace}: {exc}"
            ) from exc
        raise RemoteBackendUnavailable(
            f"Azure provider namespace {namespace} did not reach Registered state."
        )


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_image(image: str) -> dict[str, str]:
    parts = [part.strip() for part in image.split(":")]
    if len(parts) != 4 or not all(parts):
        raise RemoteBackendUnavailable(
            "azure provider image must look like "
            "'Publisher:Offer:Sku:Version'."
        )
    return {
        "publisher": parts[0],
        "offer": parts[1],
        "sku": parts[2],
        "version": parts[3],
    }


def _public_key_from_private_key(path: str | None) -> str:
    if not path:
        raise RemoteBackendUnavailable(
            "azure provider requires key=<private-key-path> or "
            "ssh_public_key=<openssh-public-key>."
        )
    proc = subprocess.run(
        ["ssh-keygen", "-y", "-f", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RemoteBackendUnavailable(
            f"Could not derive a public key from {path!r}: "
            f"{(proc.stderr or '').strip()[:500]}"
        )
    return proc.stdout.strip()


def _detect_public_cidr() -> str:
    try:
        response = requests.get("https://checkip.amazonaws.com", timeout=5)
        response.raise_for_status()
        ip = response.text.strip()
        socket.inet_aton(ip)
        return f"{ip}/32"
    except Exception as exc:
        raise RemoteBackendUnavailable(
            "azure provider could not auto-detect the orchestrator public IP. "
            "Set provider_config.ssh_cidr explicitly."
        ) from exc
