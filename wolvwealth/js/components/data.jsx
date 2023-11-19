import React, { useState } from "react";
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faBoltLightning, faTrashCan } from "@fortawesome/free-solid-svg-icons";
import { PieChart } from 'react-minimal-pie-chart';

function getRandomHexColor() {
    let letters = '0123456789ABCDEF';
    let color = '#';
    for (let i = 0; i < 6; i++) {
        color += letters[Math.floor(Math.random() * 16)];
    }
    return color;
}

function Data({ name }) {
    const algorithms = [
        "Mean Variance",
        "Black-Litterman",
        "Hierarchical Risk Parity",
        "Critical Line"
    ]
    const riskModels = [
        "Sample Covariance",
        "Semicovariance",
        "Exp Covariance",
        "Ledoit Wolf",
        "Ledoit Wolf Constant Variance",
        "Ledoit Wolf Single Factor",
        "Ledoit Wolf Constant Correlation",
        "Oracle Approximating"
    ];
    const outputTypes = [
        "shares", "weights"
    ]

    const [moneyInAccount, setMoneyInAccount] = useState(256);
    const [algorithmSelection, setAlgorithmSelection] = useState(algorithms[0]);
    const [riskModelSelection, setRiskModelSelection] = useState(algorithms[0]);
    const [outputType, setOutputType] = useState("shares"); // "weights" || "shares"
    const [algoArgs, setAlgoArgs] = useState(""); // i.e. "{'risk_aversion': 1.0}"
    const [hasOptimized, setHasOptimized] = useState(false);
    const [portfolioJSON, setPortfolioJSON] = useState({}); 
    // same as data
    const initialTickerSelection = [
        {symbol:"AAPL", count:10},
        {symbol:"AMZN", count:20},
        {symbol:"NFLX", count:30},
        {symbol:"GOOGL", count:40},
        {symbol:"TSLA", count:50},
    ];
    const [tickerSelection, setTickerSelection] = useState(initialTickerSelection); // a list of user selected tickers 
    const [newTickerNameInput, setNewTickerNameInput] = useState("");
    const [newTickerCountInput, setNewTickerCountInput] = useState(0);
    const data = [
        // set color to random hex value
        { title: 'META', value: 10, color:getRandomHexColor() },
        { title: 'AAPL', value: 20, color:getRandomHexColor() },
        { title: 'AMZN', value: 30, color:getRandomHexColor() },
        { title: 'NFLX', value: 40, color:getRandomHexColor() },
        { title: 'GOOGL', value: 50, color:getRandomHexColor() },
    ];
    const [chartData, setChartData] = useState(data);

    const handleNewTickerNameInput = (e) => {
        setNewTickerNameInput(e.target.value);
    }
    const parseJSONEntry = (e) => {
        let json = JSON.parse(e.target.value);
        setPortfolioJSON(json);
        console.log(json);
        setMoneyInAccount(json["initial_cash"]);
        setAlgorithmSelection(json["algorithm"]);
    }

    const handleAlgorithmChange = (e) => {
        setAlgorithmSelection(e.target.value);
    }
    const handleInitialCashChange = (e) => {
        console.log("updated cash amount");
        setMoneyInAccount(e.target.value);
    }
    const handleRiskModelChange = (e) => {
        console.log(e.target.value);
    }
    const handleOutputTypeChange = (e) => {
        setOutputType(e.target.value);
    }
    const handleAlgoArgsChange = (e) => {
        setAlgoArgs(e.target.value);
    }
    const handleTickerSelectionChange = (e) => {
    }
    const handleNewTickerCountInput = (e) => {
        setNewTickerCountInput(e.target.value);
    }
    const handleUpdateData = (newTickerSelection) => {
        console.log("Updating data");
        let newChartData = [];
        for (let i = 0; i < newTickerSelection.length; i++) {
            console.log("Adding " + newTickerSelection[i].symbol + " to chart data")
            newChartData.push({title:newTickerSelection[i].symbol, value:newTickerSelection[i].count, color:getRandomHexColor()});
        }
        console.log("BEFORE=",chartData);
        console.log("AFTER=",newChartData);
        setChartData(newChartData);
        console.log(chartData);
    }
    const handleAddTicker = (e) => {
        // console.log("Add ticker button clicked!");
        // let newTickerName = newTickerNameInput;
        // check if tickerSelection already contains newTicker
        // if (tickerSelection.includes({symbol:newTickerNameInput, count:newTickerCountInput})) {
        //     alert("Ticker already exists!");
        //     return;
        // }
        for(let i = 0; i < tickerSelection.length; i++) {
            if (tickerSelection[i].symbol == newTickerNameInput && tickerSelection[i].count == newTickerCountInput) {
                alert("Ticker already exists!");
                return;
            }
        }
        console.log("Add ticker");
        let newTickerSelection = tickerSelection;
        newTickerSelection.push({symbol:newTickerNameInput, count:newTickerCountInput});
        setTickerSelection(newTickerSelection);

    }

    const handleRemoveTicker = (e) => {
        console.log("Remove ticker button clicked!");
        // get symbol and count attribute from button element
        let symbol = e.target.getAttribute("symbol");
        let count = e.target.getAttribute("count");
        // let newTickerSelection = tickerSelection;
        console.log(symbol, count);
        let newTickerSelection = tickerSelection.filter((ticker) => ticker.symbol != symbol && ticker.count != count);
        setTickerSelection(newTickerSelection);
        console.log(newTickerSelection);
    }

    const handleOptimizeSubmit = (e) => {
        console.log("Optimize button clicked!");
        console.log("Algorithm: " + algorithmSelection);
        console.log("Risk Model: " + riskModelSelection);
        console.log("Output Type: " + outputType);
        console.log("Algo Args: " + algoArgs);
        console.log("Money in Account: " + moneyInAccount);
        console.log("Portfolio JSON: " + portfolioJSON);
        setHasOptimized(true);
        console.log("Trying to hit endpoint");
        let tickers = {};
        for (let i = 0; i < tickerSelection.length; i++) {
            tickers[tickerSelection[i].symbol] = tickerSelection[i].count;
        }
        fetch("/api/optimize/", {
            method: "POST",
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                // "algorithm": algorithmSelection
                "initial_cash": moneyInAccount,
                "response_type": outputType,
                "initial_holdings": tickers,
            })
        }).then((res) => {
            return res.json();
        })
        .then((data) => {
            let keys = Object.keys(data);
            // iterate over keys and add to tickerSelection
            let newTickerSelection = [];
            console.log(data);
            for (let i = 0; i < keys.length; i++) {
                console.log(data[keys[i]]["shares"])
                newTickerSelection.push({symbol:keys[i], count:data[keys[i]]["shares"]});
            }
            setTickerSelection(newTickerSelection);
            handleUpdateData(newTickerSelection);
        })
        // .except((err) => {
        //     console.log(err);
        // })
    }

    const defaultLabelStyle = {
        fontSize: '5px',
        fontFamily: 'sans-serif',
      };

    // const defaultLabelStyle = {
    //     fontSize: '5px',
    //     fontFamily: 'sans-serif',
    //   };

    const options = {
        title: "My Daily Activities",
        pieHole: 0.4,
        is3D: false,
    };


    return (
        <div className="flex flex-col p-10 bg-gray h-screen">
            <div className="flex flex-col">
                <div id="contentHeader" className="flex flex-row mb-8">
                    <h1 className="text-4xl font-bold mr-auto">
                        Your dashboard
                    </h1>
                </div>
                <div className="flex flex-grow flex-col">
                    <div id="inputRow" className="flex">
                        {/* Inputs: Money in account, Algorithm selection (dropdown), and optimize button */}
                        <div className="flex flex-row flex-wrap">
                            <div className="flex flex-col flex-grow mr-4">
                                <label htmlFor="moneyInAccount" className="text-xl">Initial Cash</label>
                                <span className="flex flex-row text-xl fira rounded-lg bg-gray-dark">
                                    <span className="pl-5 py-5">$</span>
                                    <input type="number" id="moneyInAccount" onChange={handleInitialCashChange} className="bg-gray-dark rounded-lg py-5 pr-5 w-[auto]" placeholder="0.00"></input>
                                </span>
                            </div>
                            <div className="flex flex-col flex-grow mr-4 fira text-xl">
                                <label htmlFor="algorithmSelection" className="text-xl">Algorithm</label>
                                <select id="algorithmSelection" className="rounded-lg bg-gray-dark p-5 w-[auto]" 
                                defaultValue={algorithms[0]} onChange={handleAlgorithmChange}>
                                    {
                                    algorithms.map((algorithm) => 
                                        (<option key={algorithm} value={algorithm}>{algorithm}</option>)
                                    )
                                    }
                                </select>
                            </div>
                            <div className="flex flex-col flex-grow mr-4 fira text-xl">
                                <label htmlFor="riskModelSelection" className="text-xl">Risk Model</label>
                                <select id="riskModelSelection" className="rounded-lg bg-gray-dark p-5 w-[auto]" 
                                defaultValue={riskModels[0]} onChange={handleRiskModelChange}>
                                    {
                                    riskModels.map((riskModel) => 
                                        (<option key={riskModel} value={riskModel}>{riskModel}</option>)
                                    )
                                    }
                                </select>
                            </div>
                            <div className="flex flex-col flex-grow mr-4 fira text-xl">
                                <label htmlFor="outputTypeSelection" className="text-xl">Output Type</label>
                                <select id="outputTypeSelection" className="rounded-lg bg-gray-dark p-5 w-[auto]" 
                                defaultValue={outputTypes[0]} onChange={handleOutputTypeChange}>
                                    {outputTypes.map((outputType) => 
                                        (<option key={outputType} value={outputType}>{outputType}</option>))
                                    }
                                </select>
                            </div>
                            <div className="flex flex-col">
                                <label htmlFor="optimizeButton" className="text-xl">Optimize</label>
                                <button onClick={handleOptimizeSubmit} className="text-xl rounded-lg bg-gray-dark p-5 fira  hover:bg-blue">
                                    <span className="pr-3">Optimize</span>
                                    <FontAwesomeIcon icon={faBoltLightning} />
                                </button>
                            </div>
                            <div className="flex flex-col text-xl m-10">
                                {
                                    (hasOptimized ?
                                        <span>
                                            We've optimized your portfolio!
                                        </span>
                                        :
                                        <span className="text-red">
                                            You have not optimized your portfolio yet!
                                        </span>)
                                }
                            </div>
                        </div>
                    </div>
                    <hr className="bg-gray-dark text-gray-dark font-bold"></hr>
                    <div className="grid grid-cols-1 lg:grid-cols-2 grid-rows-1 gap-8">
                        <div id="before" className="">
                            <span>Ticker</span>
                            <div className="w-[100%] h-[100%]">
                                {tickerSelection.map((ticker) => (
                                    <div key={ticker.symbol} className="flex flex-row mb-3">
                                        <div className="flex flex-row rounded-xl bg-gray-dark p-5 mr-2 w-[100%]">
                                            <span className="text-white">{ticker.symbol}</span>
                                        </div>
                                        <div className="flex flex-row rounded-xl bg-gray-dark p-5 w-[100%]">
                                            <span className="text-white">{ticker.count}</span>
                                        </div>
                                        <button onClick={handleRemoveTicker} symbol={ticker.symbol} count={ticker.count} className="flex rounded-xl p-5 ml-2 bg-gray-dark">
                                            <FontAwesomeIcon icon={faTrashCan} />
                                        </button>
                                    </div>
                                ))}
                                <div className="flex flex-row">
                                    {/* user input for tickers */}
                                    <input type="text" id="newTickerInput" onChange={handleNewTickerNameInput} placeholder="symbol" className="flex bg-gray-dark p-5 mr-2 rounded-xl w-[100%]">
                                    </input>
                                    <input type="number" id="newTickerInput" onChange={handleNewTickerCountInput} placeholder="count" className="flex bg-gray-dark p-5 rounded-xl w-[100%]">
                                    </input>
                                    <button onClick={handleAddTicker} className="flex w-[250px] justify-center text-center bg-gray-dark p-5 rounded-xl ml-2">
                                        Add
                                    </button>
                                </div>
                            </div>
                        </div>
                        <div id="after" className="">
                            <span>Insights</span>
                            <div className="text-white w-[50%] bg-gray mx-auto">

                                <PieChart
                                    data={chartData}
                                    label={({ dataEntry }) => dataEntry.title}
                                    labelPosition={110}
                                      labelStyle={{...defaultLabelStyle,}}
                                    className="h-[100%] w-[100%] mx-auto"
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
}

export default Data;