import React from "react";

function Help() {
    return (
        <div className="flex flex-col p-10 bg-gray">
            <div id="contentHeader" className="flex flex-row mb-8">
                <h1 className="text-4xl font-bold mr-auto">
                    Using WolvWealth
                </h1>
                <div>
                    <button className="mr-4"><img src="/static/img/logo.png" className="h-[30px]"></img></button>
                </div>
            </div>
            <div className="max-w-md fira">
                <p>
                    Using WolvWealth is easy! Simply click on the sidebar to navigate to the page you want to go to.
                </p>
                <p>
                    Submit your portfolio data to the dashboard page to get a detailed analysis of your portfolio. When you first upload your portfolio data, you will see the original state.
                </p>
                <p>
                    Click on the "Optimize" button and WolvWealth will generate an optimized portfolio using the selected algorithm.
                    Select your desired optimization algortithm from the dropdown menu.
                </p>
            </div>
        </div>
    )
}

export default Help;