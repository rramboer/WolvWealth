import PropTypes from "prop-types";
import OptimizerPage from "./pages/optimizer";

const App = ({ config }) => <OptimizerPage config={config} />;

App.propTypes = {
  config: PropTypes.object.isRequired,
};

export default App;
