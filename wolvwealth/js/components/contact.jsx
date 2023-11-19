import React from "react";

function Contact() {
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
            <div>
                <p>
                    Thank you for using WolvWealth! If you have any questions, contact us!
                </p>
            </div>
        </div>
    )
}

export default Contact;