"""Entry point for command line arguments"""

from casperfpga import CasperFpga
from casperfpga.tengbe import TenGbe
from casperfpga.network import IpAddress, Mac
from casperfpga.snapadc import SnapAdc
from loguru import logger
from typing import Dict
from enum import Enum
import logging
import time
import sys
import argparse

parser = argparse.ArgumentParser(
    prog="snapctl", description="SNAP bringup routines for GReX", add_help=True
)

parser.add_argument("filename", help="The FPG file to program")
parser.add_argument("ip", help="The IP address of the Pi (or proxy)")
parser.add_argument(
    "--core_ip", help="IP address of the 10 GbE Core", default="192.168.0.20"
)
parser.add_argument(
    "--core_port", help="Port of the 10 GbE Core", default=60000, type=int
)
parser.add_argument(
    "--dest_ip", help="IP address of the UDP payload destination", default="192.168.0.1"
)
parser.add_argument(
    "--dest_port", help="Port of the UDP payload destination", default=60000, type=int
)
parser.add_argument(
    "--core_mac", help="MAC address of the 10 GbE core", default="02:2E:46:E0:64:A1"
)
parser.add_argument(
    "--dest_mac",
    help="MAC address of the UDP payload destination (ARP)",
    default="98:b7:85:a7:ec:78",
)
parser.add_argument(
    "--adc_name", help="Simulink block name for the ADC", default="snap_adc"
)
parser.add_argument(
    "--core_name", help="Simulink block name for the 10 GbE core", default="gbe1"
)
parser.add_argument("--channels", help="ADC channels", default=2, type=int)


class AdcPair(Enum):
    A1_2 = 0
    A3_4 = 1
    B1_2 = 2
    B3_4 = 3
    C1_2 = 4
    C3_4 = 5


def chan_1_select(client: CasperFpga, adc_pair: AdcPair):
    """Sets the ADC input pair selected for the first channel (A)"""
    client.write_int(f"ch_1_sel", adc_pair.value)


def chan_2_select(client: CasperFpga, adc_pair: AdcPair):
    """Sets the ADC input pair selected for the second channel (B)"""
    client.write_int(f"ch_2_sel", adc_pair.value)


def set_requant_gain(client: CasperFpga, gain: int):
    assert (
        0 < gain < 2047
    ), "Gain is 11 bit unsigned integer, so gain must be between 0 and 2047"
    # Convert to fixed point
    client.write_int("requant_gain", int(round(gain * 32)))


def program_snap(filename: str, ip: str) -> CasperFpga:
    client = CasperFpga(ip)
    logger.info("SNAP connected")
    client.upload_to_ram_and_program(filename)
    logger.success("SNAP programmed")
    # We're using TAPCP, so we still need to tell casperfpga about the registers
    client.get_system_information(filename)
    return client


def setup_adcs(client: CasperFpga, adc_name: str, channels: int):
    assert (
        client.adc_devices is not None
    ), "The connected client doesn't seem to have any ADCs"
    # Type hint for adc_devices
    devices: Dict[str, SnapAdc] = client.adc_devices
    assert adc_name in devices, f"{adc_name} is not an ADC we know about"
    adc = devices[adc_name]
    assert adc.adc is not None
    # For some reason this is getting set wrong.
    # It should be None because we are using an external clock
    adc.lmx = None
    # Setup all the clocks
    # TODO: This should be handled in CasperFPGA
    adc.clksw.setSwitch("b")
    time.sleep(0.5)
    adc.logger.debug("Reseting adc_unit")
    # Clear any dangling ADC state
    adc.reset()
    # Select all ADCs
    adc.selectADC()
    # Start setting up the clocks
    adc.logger.debug("Initialising ADCs")
    adc.adc.init()
    # SNAP only uses one of the 3 ADC chips to provide clocks, so turn the others
    # to the lowest drive strength possible and terminate them
    adc.selectADC([1, 2])  # Talk to the 2nd and 3rd ADCs
    # Please refer to HMCAD1511 datasheet for more details
    # LCLK Termination
    rid, mask = adc.adc._getMask("en_lvds_term")
    val = adc.adc._set(
        0x0, 0b1, mask
    )  # Enable termination. Default terminations (i.e. none)
    rid, mask = adc.adc._getMask("term_lclk")
    val = adc.adc._set(val, 0b011, mask)  # 94 ohm
    # Frame CLK termination
    rid, mask = adc.adc._getMask("term_frame")
    val = adc.adc._set(val, 0b011, mask)  # 94 ohm
    adc.adc.write(val, rid)
    # LCLK Drive Strength
    rid, mask = adc.adc._getMask("ilvds_lclk")
    val = adc.adc._set(0x0, 0b011, mask)  # 0.5 mA. Default Data drive strength
    # Frame CLK Drive Strength
    rid, mask = adc.adc._getMask("ilvds_frame")
    val = adc.adc._set(val, 0b011, mask)  # 0.5 mA
    adc.adc.write(val, rid)
    # Select all ADCs and continue initialization
    adc.selectADC()
    # Set the operating mode
    adc.adc.setOperatingMode(channels, 1, False)

    # ADC init/lmx select messes with FPGA clock, so reprogram
    adc.logger.debug("Reprogramming the FPGA for ADCs")
    client.transport.prog_user_image()
    adc.selectADC()
    adc.logger.debug("Reprogrammed")

    # Select the clock source switch again. The reprogramming
    # seems to lose this information
    adc.logger.debug("Configuring clock source switch")
    adc.clksw.setSwitch("b")

    time.sleep(0.5)

    # Calibration - stolen from HERA because it was mysteriously removed from casper_fpga

    adc._retry_cnt = 0
    adc.working_taps = {}

    # Calibrate in full interleave
    adc.setDemux(numChannel=1)

    adc.logger.debug("Check if MMCM locked")
    if not adc.getWord("ADC16_LOCKED"):
        adc.logger.error("MMCM not locked.")
        return False

    time.sleep(0.5)

    fails = adc.alignLineClock()
    if len(fails) > 0:
        adc.logger.warning("alignLineClock failed on: " + str(fails))
    fails = adc.alignFrameClock()
    if len(fails) > 0:
        adc.logger.warning("alignFrameClock failed on: " + str(fails))
    fails = adc.rampTest()
    if len(fails) > 0:
        adc.logger.warning("rampTest failed on: " + str(fails))

    # And finish up
    adc.setDemux(numChannel=channels)
    adc.adc.selectInput([1, 1, 1, 1])
    logger.info("ADCs configured")
    adc.set_gain(4)


def setup_tengbe(
    client: CasperFpga,
    core_name: str,
    core_mac: str,
    core_ip: str,
    core_port: int,
    dest_ip: str,
    dest_port: int,
    dest_mac: str,
):
    assert (
        client.gbes is not None
    ), "The connected client doesn't seem to have any GbE cores"
    # For some (casper) reason, client.gbes isn't a normal dict
    try:
        client.gbes[core_name]
    except:
        raise KeyError(f"{core_name} is not a 10 GbE core we know about")
    # Type hint for gbes
    gbe: TenGbe
    gbe = client.gbes[core_name]
    logger.info(f"Configuring GbE Core: {core_name}")
    client.write_int("tx_en", 0)
    # Setup the core
    gbe.configure_core(core_mac, core_ip, core_port, gateway=dest_ip)
    # Set the destination
    client.write_int("dest_port", dest_port)
    client.write_int("dest_ip", IpAddress.str2ip(dest_ip))
    # Add the server to the ARP table
    gbe.set_single_arp_entry(dest_ip, Mac(dest_mac).mac_int)
    # Toggle reset
    client.write_int("tx_rst", 1)
    client.write_int("tx_rst", 0)
    # This register is ANDed with `tx_valid`, so this needs to be true to push bytes into the FIFO
    client.write_int("tx_en", 1)
    # Wait for the core to boot
    time.sleep(2)
    # Check the link
    if client.read_int("gbe1_linkup") == 1:
        logger.success("10 GbE link is up")


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level if it exists.
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def startup(
    # The FPG file
    filename: str,
    # The ip of the Pi (or proxy to the Pi)
    ip: str,
    # Many of these are set by the gateware and don't warrant changing
    core_ip: str = "192.168.0.20",
    dest_ip: str = "192.168.0.1",
    # I don't know what happens if these are different
    core_port: int = 60000,
    dest_port: int = 60000,
    # Core is arbitrary, dest *should* match the NIC of the server
    core_mac: str = "02:2E:46:E0:64:A1",
    dest_mac: str = "98:b7:85:a7:ec:78",
    # Set by the names of the simulink block
    adc_name: str = "snap_adc",
    core_name: str = "gbe1",
    channels: int = 2,
):
    # Setup logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    # Filter out the things we really don't care about
    logger.disable("casperfpga.memory")
    logger.disable("casperfpga.bitfield")
    logger.disable("casperfpga.sbram")
    logger.disable("casperfpga.register")
    logger.disable("casperfpga.utils")
    logger.disable("casperfpga.snap")
    logger.disable("asyncio")
    logger.disable("katcp")
    logger.disable("matplotlib")
    logger.disable("PIL")
    logger.disable("tftpy")
    # Program the SNAP
    client = program_snap(filename, ip)
    # Startup networking
    setup_tengbe(
        client,
        core_name,
        core_mac,
        core_ip,
        core_port,
        dest_ip,
        dest_port,
        dest_mac,
    )
    # Setup some constants
    client.write_int("fft_shift", 4095)
    chan_1_select(client, AdcPair.A1_2)
    chan_2_select(client, AdcPair.B1_2)
    set_requant_gain(client, 1)
    # Calibrate the ADCs
    setup_adcs(client, adc_name, channels)
    clk = client.estimate_fpga_clock()
    logger.success(f"Setup complete - FPGA clock at {clk} MHz")


# CLI entry point
def main():
    args = parser.parse_args()
    startup(
        args.filename,
        args.ip,
        args.core_ip,
        args.dest_ip,
        args.core_port,
        args.dest_port,
        args.core_mac,
        args.dest_mac,
        args.adc_name,
        args.core_name,
        args.channels,
    )
