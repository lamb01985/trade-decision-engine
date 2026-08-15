from dataclasses import dataclass, field


# Exchange/index membership changes over time. These bundled liquid constituents are an
# offline baseline; deployments can replace/extend either group without changing scanner logic.
NASDAQ_100 = frozenset("""AAPL ABNB ADBE ADI ADP ADSK AEP AMAT AMD AMGN AMZN APP ARM ASML AVGO AXON AZN BIIB BKNG BKR CCEP CDNS CDW CEG CHTR CMCSA COST CPRT CRWD CSCO CSGP CSX CTAS CTSH DASH DDOG DXCM EA EXC FAST FANG FTNT GFS GILD GOOG GOOGL HON IDXX INTC INTU ISRG KDP KHC KLAC LIN LRCX MAR MCHP MDB MDLZ MELI META MNST MRVL MSFT MSTR MU NFLX NVDA NXPI ODFL ON ORLY PANW PAYX PCAR PDD PEP PLTR PYPL QCOM REGN ROP ROST SBUX SHOP SNPS TEAM TMUS TSLA TTWO TXN VRSK VRTX WBD WDAY XEL ZS""".split())

# A liquidity-oriented S&P 500 baseline keeps a market-wide REST scan practical. Additional
# constituents can be supplied through SCANNER_ADDITIONAL_SYMBOLS or the request watchlist.
SP500_LIQUID = frozenset("""AAPL ABBV ABT ACN ADBE AMD AMGN AMT AMZN AVGO AXP BA BAC BKNG BLK BMY BRK.B C CAT CB CCI CHTR CI CL CMCSA COP COST CRM CSCO CVS CVX DE DHR DIS DUK EMR F FDX GD GE GILD GM GOOG GOOGL GS HD HON IBM INTC INTU ISRG JNJ JPM KO LIN LLY LMT LOW MA MCD MDLZ MDT META MMM MO MRK MS MSFT MU NEE NFLX NKE NOW NVDA ORCL PANW PEP PFE PG PLTR PM PYPL QCOM RTX SBUX SCHW SLB SO SPG T TGT TJX TMO TMUS TSLA TXN UNH UNP UPS USB V VLO VZ WFC WMT XOM""".split())


@dataclass(frozen=True)
class UniverseSelection:
    watchlist: tuple[str, ...] = ()
    include_nasdaq_100: bool = False
    include_sp_500: bool = False
    additional: tuple[str, ...] = ()

    def symbols(self) -> list[str]:
        combined = set(self.watchlist) | set(self.additional)
        if self.include_nasdaq_100:
            combined |= NASDAQ_100
        if self.include_sp_500:
            combined |= SP500_LIQUID
        return sorted(x.upper() for x in combined)
