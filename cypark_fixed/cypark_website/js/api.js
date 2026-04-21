
// SIMPLE FRONTEND LOGIN (NO CRYPTO, GITHUB SAFE)

const API = {
  login: async function(username, password) {
    if (username === "admin" && password === "admin123") {
      localStorage.setItem("user", JSON.stringify({username}));
      return { ok: true };
    }
    return { ok: false, msg: "Invalid username or password" };
  },

  getUser: function() {
    return JSON.parse(localStorage.getItem("user"));
  },

  logout: function() {
    localStorage.removeItem("user");
  }
};
