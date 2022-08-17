"""Entry point for command line arguments"""

from casperfpga import CasperFpga
from casperfpga.tengbe import TenGbe
from casperfpga.network import IpAddress, Mac
from casperfpga.snapadc import SnapAdc
from casperfpga.snap import Snap
from casperfpga.adc import HMCAD1511
from loguru import logger
from typing import Dict
from enum import Enum
import numpy as np
import matplotlib.pyplot as plt
import typer
import logging
import time
import sys
import struct

CLI = typer.Typer()


class OutputPair(Enum):
    _1_2 = 0
    _1_3 = 1
    _1_4 = 2
    _2_3 = 3
    _2_4 = 4
    _3_4 = 5


class AdcChan(Enum):
    A = 0
    B = 1
    C = 2


class OutChan(Enum):
    A = 1
    B = 2


def pair_select(client: CasperFpga, pair: OutputPair):
    client.write_int("pair_sel", pair.value)


def chan_select(client: CasperFpga, adc_chan: AdcChan, out_chan: OutChan):
    client.write_int(f"ch_{out_chan.value}_sel", adc_chan.value)


def set_requant_gain(client: CasperFpga, gain: float):
    assert (
        0 < gain < 2047
    ), "Gain is 16 bit fixed point, unsigned, with a binary point at 5, so gain must be between 0 and 2047"
    # Convert to fixed point
    client.write_int("requant_gain", int(round(gain * 32)))


def program_snap(filename: str, ip: str, upload_port: int) -> CasperFpga:
    client = CasperFpga(ip)
    logger.info("SNAP connected")
    client.upload_to_ram_and_program(filename, port=upload_port)
    logger.success("SNAP programmed")
    return client


def setup_adcs(client: CasperFpga, adc_name: str, sample_rate_mhz: int, channels: int):
    assert (
        client.adc_devices is not None
    ), "The connected client doesn't seem to have any ADCs"
    # Type hint for adc_devices
    devices: Dict[str, SnapAdc] = client.adc_devices
    assert adc_name in devices, f"{adc_name} is not an ADC we know about"
    # For some reason this is getting set wrong.
    # It should be None because we are using an external clock
    devices[adc_name].lmx = None
    # Run init (~20 seconds)
    # This need to go around a few times, let's say at most 3
    for _ in range(3):
        if devices[adc_name].init(sample_rate_mhz, channels):
            break
    adc_wrapper = devices[adc_name]
    assert adc_wrapper.adc is not None
    # Set the ADC inputs correctly
    adc_wrapper.adc.selectInput([1, 1, 1, 1])
    logger.info("ADCs configured")


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
        client.gbes != None
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
    if client.read_int(f"gbe0_linkup") == 1:
        logger.success("10 GbE link is up")
    # Wait a few cycles to see if anything is overflowing or otherwise erroneous
    time.sleep(1)
    assert client.read_uint("gbe0_txofctr") == 0, "Overflow detected in the 10 GbE Core"


@CLI.command()
def program(filename: str, ip: str, upload_port: int = 3000):
    program_snap(filename, ip, upload_port)


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


@CLI.command()
def startup(
    # The FPG file
    filename: str,
    # The ip of the Pi (or proxy to the Pi)
    ip: str,
    # Many of these are set by the gateware and don't warrant changing
    upload_port: int = 3000,
    core_ip: str = "192.168.5.20",
    dest_ip: str = "192.168.5.1",
    # I don't know what happens if these are different
    core_port: int = 60000,
    dest_port: int = 60000,
    # Core is arbitrary, dest *should* match the NIC of the server
    core_mac: str = "02:2E:46:E0:64:A1",
    dest_mac: str = "98:03:9b:3d:8b:7a",
    # Set by the names of the simulink block
    adc_name: str = "snap_adc",
    core_name: str = "gbe0",
    # This requires a 500 MHz clock in the sample input
    sample_rate_mhz: int = 500,
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
    client = program_snap(filename, ip, upload_port)
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
    pair_select(client, OutputPair._1_2)
    chan_select(client, AdcChan.A, OutChan.A)
    chan_select(client, AdcChan.B, OutChan.B)
    set_requant_gain(client, 1.0)
    # Calibrate the ADCs
    setup_adcs(client, adc_name, sample_rate_mhz, channels)
    clk = client.estimate_fpga_clock()
    logger.success(f"Setup complete - FPGA clock at {clk} MHz")
