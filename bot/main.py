# https://www.quantconnect.com/tutorials/introduction-to-options/quantconnect-options-api
from datetime import timedelta, datetime
import pytz
from MyQC500UniverseSelectionModel import MyQC500UniverseSelectionModel
class OptionsAlgo(QCAlgorithm):

    symbolBenchmark = "SPY"
    daysForward = 7
    daysForwardTotal = 30
    resolution = Resolution.Minute # options only accept minute, so we need to manually resolve this
    maxPrice = 100
    slippageAdj = .03
    orderFee = 1
    minOptionContractPremium = .5
    minOptionContractProfit = .25
    minOptionContractProfitPct = 0
    minOptionContractProfitPctPerDay = .015
    minOptionContractProfitRangePct = 0
    minOptionContractProfitRangePctPerDay = .01
    contractsSortBy = "_ProfitSumPctPerDay"
    universeAmt = 100

    def Initialize(self):
        self.Debug(str(self.Time) + " Initialize.")
        self.now = datetime.now()
        self.tz = pytz.timezone(self.TimeZone.Id)
        self.UniverseSettings.Resolution = OptionsAlgo.resolution
        self.UniverseSettings.DataNormalizationMode = DataNormalizationMode.Raw
        # self.SetSecurityInitializer(lambda x: x.SetDataNormalizationMode(DataNormalizationMode.Raw))
        self.MyQC500 = MyQC500UniverseSelectionModel(True, self.UniverseSettings)
        self.AddUniverseSelection(FineFundamentalUniverseSelectionModel(self.MyQC500CoarseSelectionFunction, self.MyQC500FineSelectionFunction))
        # self.AddUniverse(self.CoarseSelectionFunction)
        # self.AddUniverseSelection(FineFundamentalUniverseSelectionModel(self.MyQC500)) # DOESN'T WORK
        # self.AddUniverse(self.Universe.Index.QC500)
        # self.AddUniverse(self.Universe.DollarVolume.Top(OptionsAlgo.universeAmt))
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2020, 1, 16)
        # self.SetEndDate(2021, 1, 1)
        # self.SetEndDate(self.now + timedelta(days = OptionsAlgo.daysForwardTotal))
        self.DateBeforeEndDate = self.EndDate.date()# - timedelta(days=1)
        self.SetCash(10000)
        self.equityOrderTicket = False
        self.optionOrderTicket = False
        self.optionOrderObj = False
        self.Transactions.MarketOrderFillTimeout = timedelta(seconds=30)
        # self.SetWarmUp(60)
        self.AddEquity(OptionsAlgo.symbolBenchmark, OptionsAlgo.resolution)
        # self.SetBenchmark(OptionsAlgo.symbolBenchmark)
        self.Schedule.On(self.DateRules.EveryDay(), self.TimeRules.AfterMarketOpen(OptionsAlgo.symbolBenchmark, -5), self._OnStartOfDay)
        self.Schedule.On(self.DateRules.On(self.DateBeforeEndDate.year, self.DateBeforeEndDate.month, self.DateBeforeEndDate.day), self.TimeRules.BeforeMarketClose(OptionsAlgo.symbolBenchmark, 15), self.LiquidateAll)
        # self.Schedule.On(self.DateRules.Every(DayOfWeek.Monday, DayOfWeek.Monday), self.TimeRules.At(10, 0), self.Rebalance)

    # def OnWarmupFinished(self):
    #     if not self.Portfolio[self.option.Symbol].Invested:
    #         self.MarketOnOpenOrder(self.option.Symbol, 100)

    def _OnStartOfDay(self):
        self.TodayDate = self.Time.date()

    def OnData(self, slice):
        self.TimeOffsetAware = self.Time.astimezone(self.tz)

        if self.equityOrderTicket and (self.optionOrderTicket and (self.optionOrderTicket.Status == OrderStatus.Filled)) and self.Securities.ContainsKey(self.equityOrderTicket.Symbol) and (self.Securities[self.equityOrderTicket.Symbol].Price < (self.equityOrderTicket.AverageFillPrice - self.optionOrderTicket.AverageFillPrice)):
            self.LiquidateAll() # if we're in the red, cut our losses
            return

        if (self.equityOrderTicket and (self.equityOrderTicket.Status != OrderStatus.Filled) and ((self.equityOrderTicket.Time - self.TimeOffsetAware).seconds * 60 > 1)) or (self.optionOrderTicket and (self.optionOrderTicket.Status != OrderStatus.Filled) and ((self.optionOrderTicket.Time - self.TimeOffsetAware).seconds * 60 > 1)):
            self.LiquidateAll()

        if (self.Time.minute % 30) != 1: return
        self.Debug(str(self.Time) + " OnData.")

        if self.optionOrderTicket:
            portfolioOption = self.Portfolio[self.optionOrderTicket.Symbol]
            isInvested = portfolioOption.Invested or (portfolioOption.Quantity > 0)
            if not isInvested:
                self.LiquidateAll() # may have been assigned/expired

        bestContracts = []
        budget = self.Portfolio.TotalPortfolioValue / 105
        for chain in slice.OptionChains.Values:
            if not self.ActiveSecurities.ContainsKey(chain.Underlying.Symbol): continue
            if self.Securities[chain.Underlying.Symbol].IsTradable == False:
                self.Debug(str(self.Time) + " IsTradable == False: " + chain.Underlying.Symbol.Value)
                continue
            underlyingPrice = chain.Underlying.Price + OptionsAlgo.slippageAdj
            if underlyingPrice > OptionsAlgo.maxPrice: continue
            if underlyingPrice > budget: continue

            calls = [x for x in chain if x.Right == 0]
            # puts = [x for x in chain if x.Right == 1]

            # filter
            underlyingPrice25 = (underlyingPrice * .25)
            contracts = []
            for x in calls:
                if abs(underlyingPrice - x.Strike) > underlyingPrice25: continue # Near The Money (skip further than 25% from the money)
                if not self._CalcProfit(x): continue
                # self.Debug(str(x))
                contracts.append(x)
            if len(contracts) == 0: continue

            # sort
            contracts = sorted(contracts, key=lambda x:getattr(x, OptionsAlgo.contractsSortBy), reverse=True)
            # contracts = sorted(contracts, key=lambda x:x.ImpliedVolatility, reverse=True)
            bestContracts.append(contracts[0])

        if len(bestContracts) == 0:
            return
        contracts = sorted(bestContracts, key=lambda x:getattr(x, OptionsAlgo.contractsSortBy), reverse=True)
        contract = contracts[0]
        self.Debug(str(self.Time) + " bestContract: " + str(contract))

        if self.Portfolio[contract.Symbol].Invested: return # already got it

        optionOnly = self.Portfolio[contract.UnderlyingSymbol].Invested
        equityAmountCur = 0
        optionAmount = int(self.Portfolio.TotalPortfolioValue / 101 / contract.UnderlyingLastPrice)
        equityAmount = optionAmount
        if optionOnly:
            equityAmountCur = self.equityOrderTicket.Quantity / 100
            optionAmount = max(optionAmount, equityAmountCur)
            equityAmount -= equityAmountCur

        if self.optionOrderTicket: # TODO: fix this?
            eBuy = round(self.equityOrderTicket.AverageFillPrice, 2)
            oSell = round(self.optionOrderTicket.AverageFillPrice, 2)
            oBuy = round((self.Securities[self.optionOrderTicket.Symbol].AskPrice + OptionsAlgo.slippageAdj), 2)
            eSell = round((self.Securities[self.equityOrderTicket.Symbol].Price - OptionsAlgo.slippageAdj), 2)
            profitClose = ((eSell - eBuy) + (oSell - oBuy)) # * -self.optionOrderTicket.Quantity * 100
            self.Debug(str(self.Time) + ". eSell: " + str(eSell) + ". eBuy: " + str(eBuy) + ". oSell: " + str(oSell) + ". oBuy: " + str(oBuy) + ". profitClose: " + str(profitClose))
            # profitOpen = optionAmount * contract.BidPrice # contract._Profit # ((contract.UnderlyingLastPrice * 100) - contract.BidPrice)
            if profitClose <= 0: # (profitClose + profitOpen) > 0:
                self.Debug("profitClose <= 0")
                return # not worth it

        self.LiquidateAll(optionOnly)

        self.optionOrderObj = {
            's': contract.Symbol,
            'p': contract.BidPrice,
            'a': optionAmount
        }
        if equityAmount > 0:
            self.Debug(str(self.Time) + " LimitOrder: equity")
            self.equityOrderTicket = self.LimitOrder(contract.UnderlyingSymbol, 100 * equityAmount, contract.UnderlyingLastPrice) # OrderTicket
        else:
            self.OrderOption()

    def _CalcProfit(self, x):
        x.BidPrice -= OptionsAlgo.slippageAdj
        if x.BidPrice < OptionsAlgo.minOptionContractPremium: return False
        x.UnderlyingLastPrice += OptionsAlgo.slippageAdj
        costBasis = (x.UnderlyingLastPrice - x.BidPrice)
        if costBasis <= 0:
            self.Error("costBasis <= 0: " + costBasis + str(x))
            return False
        x._Profit = (x.Strike - costBasis)
        if x._Profit < OptionsAlgo.minOptionContractProfit: return False
        x._ProfitPct = x._Profit / costBasis
        if x._ProfitPct < OptionsAlgo.minOptionContractProfitPct: return False
        days = max(0.5, (x.Expiry - self.Time).days)
        x._ProfitPctPerDay = x._ProfitPct / days
        if x._ProfitPctPerDay < OptionsAlgo.minOptionContractProfitPctPerDay: return False
        x._ProfitRangePct = x.BidPrice / x.UnderlyingLastPrice
        if x._ProfitRangePct < OptionsAlgo.minOptionContractProfitRangePct: return False
        x._ProfitRangePctPerDay = x._ProfitRangePct / days
        if x._ProfitRangePctPerDay < OptionsAlgo.minOptionContractProfitRangePctPerDay: return False

        x._ProfitSumPctPerDay = x._ProfitPctPerDay + x._ProfitRangePctPerDay
        return True

    def OrderOption(self):
        self.Debug(str(self.Time) + " LimitOrder: option")
        self.optionOrderTicket = self.LimitOrder(self.optionOrderObj['s'], -self.optionOrderObj['a'], self.optionOrderObj['p']) # OrderTicket
        self.optionOrderObj = False

    def OnOrderEvent(self, orderEvent):
        self.Debug(str(self.Time) + " OnOrderEvent. " + str(orderEvent))
        self.Log(str(self.Time) + " OnOrderEvent. " + str(orderEvent))
        if self.equityOrderTicket and self.optionOrderObj and (orderEvent.OrderId == self.equityOrderTicket.OrderId) and (orderEvent.Status == OrderStatus.Filled):
            self.OrderOption()

    def OnSecuritiesChanged(self, changes):
        for x in changes.AddedSecurities:
            # self.Debug(str(self.Time) + " OnSecuritiesChanged.AddedSecurities:" + x.Symbol.Value)
            if x.Symbol.Value == 'SPY': continue
            if x.Symbol.SecurityType != SecurityType.Equity: continue

            option = self.AddOption(x.Symbol.Value)
            option.SetFilter(lambda universe: universe.IncludeWeeklys().Strikes(-5, +5).Expiration(timedelta(0), timedelta(self.daysForward)))

        for x in changes.RemovedSecurities:
            # self.Debug(str(self.Time) + " OnSecuritiesChanged.RemovedSecurities:" + x.Symbol.Value)
            if x.Symbol.Value == 'SPY': continue
            if self.Portfolio[x.Symbol].Invested: continue
            
            for symbol in self.Securities.Keys:
                if symbol.SecurityType == SecurityType.Option and symbol.Underlying == x.Symbol:
                    self.RemoveSecurity(symbol)
                    self.RemoveSecurity(x.Symbol)
                    # self.Log(f'{self.Time} >> {x.Symbol} and {symbol} removed from the Universe')

    def LiquidateAll(self, optionOnly=False):
        self.Debug(str(self.Time) + " LiquidateAll.")
        if optionOnly:
            self.Liquidate(self.optionOrderTicket.Symbol)
        else:
            self.Liquidate()
            self.equityOrderTicket = False
        self.optionOrderTicket = False
        self.optionOrderObj = False

    # def CoarseSelectionFunction(self, coarse):
    #     # sort descending by daily dollar volume
    #     u = [x for x in coarse if x.HasFundamentalData and (x.Price < OptionsAlgo.maxPrice)]
    #     u = sorted(u, key=lambda x:x.Volume, reverse=True)
    #     # return the symbol objects of the top entries from our sorted collection
    #     return [x.Symbol for x in u[:OptionsAlgo.universeAmt]]

    def MyQC500CoarseSelectionFunction(self, coarse):
        return self.MyQC500.SelectCoarse(self, coarse)

    def MyQC500FineSelectionFunction(self, fine):
        return self.MyQC500.SelectFine(self, fine)

    def OnEndOfAlgorithm(self):
        # Get a log of the security symbols 
        self.Log(str(self.UniverseManager.ActiveSecurities))
        self.LiquidateAll()