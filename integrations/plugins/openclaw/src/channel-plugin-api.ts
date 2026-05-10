// Re-export the channel plugin under the bundle path the entry contract
// references. Keeping this file thin lets OpenClaw load the plugin without
// pulling the full runtime tree during discovery.
export { shadownetPlugin } from "./channel";
