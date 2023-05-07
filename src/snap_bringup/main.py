"""Entry point for command line arguments"""

from casperfpga import CasperFpga
from casperfpga.transport_tapcp import TapcpTransport
from casperfpga.snapadc import SnapAdc
from loguru import logger
from enum import Enum
import logging
import sys
import argparse

parser = argparse.ArgumentParser(
    prog="snap_bringup", description="SNAP bringup routines for GReX", add_help=True
)
parser.add_argument("filename", help="The FPG file to program")
parser.add_argument("ip", help="The IP address of the Pi (or proxy)")
parser.add_argument(
    "--adc_name", help="Simulink block name for the ADC", default="snap_adc"
)
parser.add_argument("--channels", help="ADC channels", default=2, type=int)
parser.add_argument("--gain", help="ADC gain", default=50, type=float)


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


def program_snap(filename: str, ip: str) -> CasperFpga:
    client = CasperFpga(ip, transport = TapcpTransport)
    logger.info("SNAP connected")
    client.upload_to_ram_and_program(filename)
    logger.success("SNAP programmed")
    # We're using TAPCP, so we still need to tell casperfpga about the registers
    client.get_system_information(filename)
    return client


def setup_adcs(client: CasperFpga, adc_name: str, channels: int, gain: float):
    # init adc and clk
    adc: SnapAdc = client.adcs[adc_name]
    adc.ref = None
    adc.selectADC()
    adc.init(sample_rate=500, numChannel=channels)
    adc.rampTest(retry=True)
    # First channel crossbar to 1 and 2 (2 is unused), as in, the input is ADC0
    adc.selectADC(0)
    adc.adc.selectInput([1, 1, 2, 2])
    # Second channel crossbar to 2 and 3 (3 is unused), as in, the input is ADC5
    adc.selectADC(1)
    adc.adc.selectInput([2, 2, 3, 3])
    adc.selectADC()
    adc.set_gain(gain)
    logger.success("ADCs configured")


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
    # Set by the names of the simulink block
    adc_name: str = "snap_adc",
    channels: int = 2,
    # Programmable ADC gain
    gain: float = 50,
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
    # Calibrate the ADCs
    setup_adcs(client, adc_name, channels, gain)
    # Setup some constants
    chan_1_select(client, AdcPair.A1_2)
    chan_2_select(client, AdcPair.B1_2)
    clk = client.estimate_fpga_clock()
    logger.success(f"Setup complete - FPGA clock at {clk} MHz")


# CLI entry point
def main():
    args = parser.parse_args()
    startup(args.filename, args.ip, args.adc_name, args.channels, args.gain)
