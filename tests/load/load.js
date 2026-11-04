import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  stages: [
    { duration: "1m", target: 20 },
    { duration: "3m", target: 20 },
    { duration: "1m", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<800"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";

export default function () {
  const response = http.get(`${BASE_URL}/api/v1/cells/nearby?lat=4.65&lon=-74.08&radius_m=2000`);
  check(response, { "status 200": (r) => r.status === 200 });
  sleep(1);
}
