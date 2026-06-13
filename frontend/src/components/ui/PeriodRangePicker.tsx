import { Select } from "@/components/ui/Select";
import { DATE_RANGE_OPTIONS } from "@/lib/dateUtils";
import { cn } from "@/lib/utils";
import {
     isValidRangePreset,
     type PeriodFilter,
} from "@/services/statementsApi";

interface PeriodRangePickerProps {
     period: PeriodFilter;
     onPeriodChange: (period: PeriodFilter) => void;
     placeholder?: string;
     wrapperClassName?: string;
     selectWrapperClassName?: string;
     selectClassName?: string;
}

export function PeriodRangePicker({
     period,
     onPeriodChange,
     placeholder = "Select range",
     wrapperClassName,
     selectWrapperClassName,
     selectClassName,
}: PeriodRangePickerProps) {
     const { range, dateFrom, dateTo } = period;

     const handleRangeChange = (value: string) => {
          if (!isValidRangePreset(value)) return;
          onPeriodChange(
               value === "custom"
                    ? { range: value, dateFrom, dateTo }
                    : { range: value },
          );
     };

     return (
          <div className={cn("flex flex-col gap-3", wrapperClassName)}>
               <div className="flex flex-wrap items-center gap-2">
                    {range === "custom" && (
                         <div className="flex flex-wrap items-center gap-2">
                              <input
                                   type="date"
                                   aria-label="Custom range start date"
                                   value={dateFrom ?? ""}
                                   max={dateTo}
                                   onChange={(e) =>
                                        onPeriodChange({
                                             range,
                                             dateFrom: e.target.value || undefined,
                                             dateTo,
                                        })
                                   }
                                   className="rounded-lg border border-gray-300 bg-gray-50 px-3 py-3 text-base text-gray-900"
                              />
                              <span className="text-gray-500">to</span>
                              <input
                                   type="date"
                                   aria-label="Custom range end date"
                                   value={dateTo ?? ""}
                                   min={dateFrom}
                                   onChange={(e) =>
                                        onPeriodChange({
                                             range,
                                             dateFrom,
                                             dateTo: e.target.value || undefined,
                                        })
                                   }
                                   className="rounded-lg border border-gray-300 bg-gray-50 px-3 py-3 text-base text-gray-900"
                              />
                         </div>
                    )}

                    <Select
                         options={DATE_RANGE_OPTIONS}
                         value={range}
                         onChange={(event) => handleRangeChange(event.target.value)}
                         wrapperClassName={selectWrapperClassName ?? "min-w-[220px]"}
                         className={selectClassName}
                         placeholder={placeholder}
                    />
               </div>
          </div>
     );
}
