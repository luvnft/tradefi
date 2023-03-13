import datetime
import logging

import pandas as pd
from pandas_ta import ema
from tradeexecutor.state.state import State
from tradeexecutor.state.trade import TradeExecution
from tradeexecutor.state.visualisation import PlotKind
from tradeexecutor.strategy.cycle import CycleDuration
from tradeexecutor.strategy.execution_context import ExecutionContext
from tradeexecutor.strategy.pandas_trader.position_manager import PositionManager
from tradeexecutor.strategy.pricing_model import PricingModel
from tradeexecutor.strategy.strategy_module import (
    ReserveCurrency,
    StrategyType,
    TradeRouting,
)
from tradeexecutor.strategy.trading_strategy_universe import (
    TradingStrategyUniverse,
    load_pair_data_for_single_exchange,
)
from tradeexecutor.strategy.universe_model import UniverseOptions
from tradingstrategy.chain import ChainId
from tradingstrategy.client import Client
from tradingstrategy.timebucket import TimeBucket
from tradingstrategy.universe import Universe

# NOTE: this setting has currently no effect
TRADING_STRATEGY_ENGINE_VERSION = "0.1"

# NOTE: this setting has currently no effect
TRADING_STRATEGY_TYPE = StrategyType.managed_positions

TRADE_ROUTING = TradeRouting.uniswap_v2_usdc

TRADING_STRATEGY_CYCLE = CycleDuration.cycle_1h

RESERVE_CURRENCY = ReserveCurrency.usdc

CANDLE_TIME_BUCKET = TimeBucket.h1

CHAIN_ID = ChainId.po

EXCHANGE_SLUG = "sushi"

TRADING_PAIR = ("WETH", "USDC")

POSITION_SIZE = 0.70

BATCH_SIZE = 90

SLOW_EMA_CANDLE_COUNT = 10
FAST_EMA_CANDLE_COUNT = 3


START_AT = datetime.datetime(2022, 1, 1)

END_AT = datetime.datetime(2022, 12, 30)

INITIAL_DEPOSIT = 10_000

STOP_LOSS_PCT = 0.993

STOP_LOSS_TIME_BUCKET = TimeBucket.m15


logger = logging.getLogger(__name__)


def decide_trades(
    timestamp: pd.Timestamp,
    universe: Universe,
    state: State,
    pricing_model: PricingModel,
    cycle_debug_data: dict,
) -> list[TradeExecution]:
    pair = universe.pairs.get_single()

    cash = state.portfolio.get_current_cash()

    candles: pd.DataFrame = universe.candles.get_single_pair_data(
        timestamp, sample_count=BATCH_SIZE
    )

    close_prices = candles["close"]

    slow_ema_series = ema(close_prices, length=SLOW_EMA_CANDLE_COUNT)
    fast_ema_series = ema(close_prices, length=FAST_EMA_CANDLE_COUNT)

    if slow_ema_series is None or fast_ema_series is None:
        # Cannot calculate EMA, because
        # not enough samples in backtesting
        logger.warning("slow_ema_series or fast_ema_series None")
        return []

    if len(slow_ema_series) < 2 or len(fast_ema_series) < 2:
        # We need at least two data points to determine if EMA crossover (or crossunder)
        # occurred at current timestamp.
        logger.warning("series too short")
        return []

    slow_ema_latest = slow_ema_series.iloc[-1]
    fast_ema_latest = fast_ema_series.iloc[-1]
    price_latest = close_prices.iloc[-1]

    # Compute technical indicators needed for trade decisions.
    slow_ema_crossover = (
        close_prices.iloc[-3] < slow_ema_series.iloc[-2]
        and price_latest > slow_ema_latest
    )
    slow_ema_crossunder = (
        close_prices.iloc[-2] > slow_ema_series.iloc[-2]
        and price_latest < slow_ema_latest
    )
    fast_ema_crossunder = (
        close_prices.iloc[-2] > fast_ema_series.iloc[-2]
        and price_latest < fast_ema_latest
    )

    trades = []

    position_manager = PositionManager(timestamp, universe, state, pricing_model)

    if not position_manager.is_any_open():
        # No open positions, decide if BUY in this cycle.
        # We buy if we just crossed over the slow EMA or if this is a very first
        # trading cycle and the price is already above the slow EMA.

        logger.trade("Starting a new trade")

        if (
            slow_ema_crossunder
            or price_latest < slow_ema_latest
            and timestamp == START_AT
        ):
            buy_amount = cash * POSITION_SIZE
            new_trades = position_manager.open_1x_long(
                pair, buy_amount, stop_loss_pct=STOP_LOSS_PCT
            )
            trades.extend(new_trades)
    else:
        logger.trade("Checking for close")

        # We have an open position, decide if SELL in this cycle.
        # We do that if we fall below any of the two moving averages.
        if slow_ema_crossover or (
            fast_ema_crossunder and fast_ema_latest > slow_ema_latest
        ):
            new_trades = position_manager.close_all()
            assert len(new_trades) == 1
            trades.extend(new_trades)

    # Visualize strategy
    # See available Plotly colours here
    # https://community.plotly.com/t/plotly-colours-list/11730/3?u=miohtama
    visualisation = state.visualisation
    visualisation.plot_indicator(
        timestamp,
        "Slow EMA",
        PlotKind.technical_indicator_on_price,
        slow_ema_latest,
        colour="green",
    )
    visualisation.plot_indicator(
        timestamp,
        "Fast EMA",
        PlotKind.technical_indicator_on_price,
        fast_ema_latest,
        colour="red",
    )

    return trades


def create_trading_universe(
    ts: datetime.datetime,
    client: Client,
    execution_context: ExecutionContext,
    universe_options: UniverseOptions,
) -> TradingStrategyUniverse:
    dataset = load_pair_data_for_single_exchange(
        client,
        execution_context,
        CANDLE_TIME_BUCKET,
        CHAIN_ID,
        EXCHANGE_SLUG,
        [TRADING_PAIR],
        universe_options,
        stop_loss_time_bucket=STOP_LOSS_TIME_BUCKET,
    )

    # Filter down to the single pair we are interested in
    universe = TradingStrategyUniverse.create_single_pair_universe(
        dataset,
        CHAIN_ID,
        EXCHANGE_SLUG,
        TRADING_PAIR[0],
        TRADING_PAIR[1],
    )

    return universe
