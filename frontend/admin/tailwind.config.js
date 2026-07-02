export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ember: "#dc5f3d",
        ink: "#132028",
        mist: "#f3efe6",
        sage: "#9bb8a6",
        steel: "#5f7582",
      },
      fontFamily: {
        display: ["Georgia", "serif"],
        body: ["'Segoe UI'", "sans-serif"],
      },
      boxShadow: {
        card: "0 18px 48px rgba(19, 32, 40, 0.12)",
      },
    },
  },
  plugins: [],
};
