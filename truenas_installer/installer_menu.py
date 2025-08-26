import asyncio
import os
import sys

import humanfriendly

from .dialog import dialog_checklist, dialog_menu, dialog_msgbox, dialog_password, dialog_yesno
from .disks import Disk, list_disks
from .exception import InstallError
from .install import install
from .serial import serial_sql


class InstallerMenu:
    def __init__(self, installer):
        self.installer = installer

    async def run(self):
        await self._main_menu()

    async def _main_menu(self):
        await dialog_menu(
            f"{self.installer.vendor} {self.installer.version} Console Setup",
            {
                "Install/Upgrade": self._install_upgrade,
                "Shell": self._shell,
                "Reboot System": self._reboot,
                "Shutdown System": self._shutdown,
            }
        )

    async def _install_upgrade(self):
        while True:
            await self._install_upgrade_internal()
            await self._main_menu()

    async def _install_upgrade_internal(self):
        disks = await list_disks()
        vendor = self.installer.vendor

        if not disks:
            await dialog_msgbox("Choose Destination Media", "No drives available")
            return False

        while True:
            destination_disks = await dialog_checklist(
                "Choose Destination Media",
                (
                    f"Install {vendor} to a drive. If desired, select multiple drives to provide redundancy. {vendor} "
                    "installation drive(s) are not available for use in storage pools. Use arrow keys to navigate "
                    "options. Press spacebar to select."
                ),
                {
                    disk.name: " ".join([
                        disk.model[:15].ljust(15, " "),
                        disk.label[:15].ljust(15, " "),
                        "--",
                        humanfriendly.format_size(disk.size, binary=True)
                    ])
                    for disk in disks
                }
            )

            if destination_disks is None:
                # Installation cancelled
                return False

            if not destination_disks:
                await dialog_msgbox(
                    "Choose Destination Media",
                    "Select at least one disk to proceed with the installation.",
                )
                continue

            wipe_disks = [
                disk.name
                for disk in disks
                if (
                    any(zfs_member.pool == "boot-pool" for zfs_member in disk.zfs_members) and
                    disk.name not in destination_disks
                )
            ]
            if wipe_disks:
                # The presence of multiple `boot-pool` disks with different guids leads to boot pool import error
                text = "\n".join([
                    f"Disk(s) {', '.join(wipe_disks)} contain existing TrueNAS boot pool, but they were not "
                    f"selected for TrueNAS installation. This configuration will not work unless these disks "
                    "are erased.",
                    "",
                    f"Proceed with erasing {', '.join(wipe_disks)}?"
                ])
                if not await dialog_yesno("TrueNAS Installation", text):
                    continue

            break

        text = "\n".join([
            "WARNING:",
            f"- This erases ALL partitions and data on {', '.join(sorted(wipe_disks + destination_disks))}.",
            f"- {', '.join(destination_disks)} will be unavailable for use in storage pools.",
            "",
            "NOTE:",
            "- Installing on SATA, SAS, or NVMe flash media is recommended.",
            "  USB flash sticks are discouraged.",
            "",
            "Proceed with the installation?"
        ])
        if not await dialog_yesno(f"{self.installer.vendor} Installation", text):
            return False

        if vendor == "HexOS":
            authentication_method = await self._authentication_truenas_admin()
        else:
            authentication_method = await dialog_menu(
                "Web UI Authentication Method",
                {
                    "Administrative user (truenas_admin)": self._authentication_truenas_admin,
                    "Root user (not recommended)": self._authentication_root,
                    "Configure using Web UI": self._authentication_webui,
                }
            )
            if authentication_method is False:
                return False

        set_pmbr = False
        if not self.installer.efi:
            set_pmbr = await dialog_yesno(
                "Legacy Boot",
                (
                    "Allow EFI boot? Enter Yes for systems with newer components such as NVMe devices. Enter No when "
                    "system hardware requires legacy BIOS boot workaround."
                ),
            )

        # If the installer was booted with serial mode enabled, we should save these values to the installed system
        sql = await serial_sql()

        try:
            await install(
                self._select_disks(disks, destination_disks),
                self._select_disks(disks, wipe_disks),
                set_pmbr,
                authentication_method,
                None,
                sql,
                self._callback,
            )
        except InstallError as e:
            await dialog_msgbox("Installation Error", e.message)
            return False

        await dialog_msgbox(
            "Installation Succeeded",
            (
                f"The {self.installer.vendor} installation on {', '.join(destination_disks)} succeeded!\n"
                "Please reboot and remove the installation media."
            ),
        )
        return True

    def _select_disks(self, disks: list[Disk], disks_names: list[str]):
        disks_dict = {disk.name: disk for disk in disks}
        return [disks_dict[disk_name] for disk_name in disks_names]

    async def _authentication_truenas_admin(self):
        return await self._authentication_password(
            "truenas_admin",
            "Enter your \"truenas_admin\" user password. Root password login will be disabled.",
        )

    async def _authentication_root(self):
        return await self._authentication_password(
            "root",
            "Enter your root password.",
        )

    async def _authentication_password(self, username, title):
        password = await dialog_password(title)
        if password is None:
            return False

        return {"username": username, "password": password}

    async def _authentication_webui(self):
        return None

    async def _shell(self):
        os._exit(1)

    async def _reboot(self):
        process = await asyncio.create_subprocess_exec("reboot")
        await process.communicate()

    async def _shutdown(self):
        process = await asyncio.create_subprocess_exec("shutdown", "now")
        await process.communicate()

    def _callback(self, progress, message):
        sys.stdout.write(f"[{int(progress * 100)}%] {message}\n")
        sys.stdout.flush()
