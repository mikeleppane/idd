defmodule Sample.MixProject do
  use Mix.Project

  def project do
    [app: :sample, version: "0.1.0", deps: deps()]
  end

  defp deps do
    [
      {:phoenix, "~> 1.7"},
      {:ecto, "~> 3.10"},
    ]
  end
end
