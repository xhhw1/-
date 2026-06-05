import type { CSSProperties } from "react";
import "./ShinyText.css";

type ShinyTextProps = {
  text: string;
  disabled?: boolean;
  speed?: number;
  className?: string;
  color?: string;
  shineColor?: string;
  spread?: number;
  yoyo?: boolean;
  pauseOnHover?: boolean;
  direction?: "left" | "right";
  delay?: number;
};

type ShinyStyle = CSSProperties & Record<string, string | number>;

export default function ShinyText({
  text,
  disabled = false,
  speed = 2,
  className = "",
  color = "#b5b5b5",
  shineColor = "#ffffff",
  spread = 120,
  yoyo = false,
  pauseOnHover = false,
  direction = "left",
  delay = 0
}: ShinyTextProps) {
  const style: ShinyStyle = {
    "--shiny-color": color,
    "--shiny-shine": shineColor,
    "--shiny-spread": `${spread}deg`,
    "--shiny-duration": `${speed}s`,
    "--shiny-delay": `${delay}s`
  };
  const classes = [
    "shiny-text",
    disabled ? "disabled" : "",
    yoyo ? "yoyo" : "",
    pauseOnHover ? "pause-on-hover" : "",
    direction === "right" ? "right" : "",
    className
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={classes} style={style}>
      {text}
    </span>
  );
}
