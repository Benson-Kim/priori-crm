import { Select } from "@/components/ui/Select";
import { CalendarPicker } from "@/components/ui/CalendarPicker";
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
                              <CalendarPicker
                                   value={dateFrom}
                                   onChange={(d) =>
                                        onPeriodChange({
                                             range,
                                             dateFrom: d || undefined,
                                             dateTo,
                                        })
                                   }
                                   max={dateTo}
                                   placeholder="From date"
                                   aria-label="Custom range start date"
                              />
                              <span className="text-gray-500">to</span>
                              <CalendarPicker
                                   value={dateTo}
                                   onChange={(d) =>
                                        onPeriodChange({
                                             range,
                                             dateFrom,
                                             dateTo: d || undefined,
                                        })
                                   }
                                   min={dateFrom}
                                   placeholder="To date"
                                   aria-label="Custom range end date"
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
